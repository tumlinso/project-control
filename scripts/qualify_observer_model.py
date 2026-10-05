#!/usr/bin/env python3
"""Bounded, controller-leased Qwen observer calibration; dry-run is the default."""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

SKILLS = Path('/home/tumlinson/.agents/skills/local-coding-worker')
DEFAULT_BUDGET = 900
VISIBLE_LIMIT = 2048
TURN_LIMIT = 60
PAIR_BYTES_MIN = 1024

# A bounded fixture with a retrieval and a two-source join, plus an explicit
# uncertainty check. It tests grounded JSON generation without repository I/O.
MESSAGES = [
    {'role': 'system', 'content': (
        'Answer only from the supplied records. Return one JSON object with keys '
        '"answer", "citations", and "uncertainty". Cite record IDs exactly. '
        'For unsupported claims, state uncertainty rather than guessing.')},
]


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(temp, path)


def load_receipt(expected: list[str]) -> dict:
    raw = os.environ.get('TODO_GPU_LEASE_RECEIPT')
    if not raw:
        raise RuntimeError('TODO_GPU_LEASE_RECEIPT is required for --go-real')
    path = Path(raw).resolve(strict=True)
    receipt = json.loads(path.read_text(encoding='utf-8'))
    ids = receipt.get('resource_ids')
    if (receipt.get('format') != 'CUDA-FOREGROUND-LEASE/1' or receipt.get('state') != 'active'
            or not isinstance(ids, list)
            or sorted(x.removeprefix('accelerator:') for x in ids) != sorted(expected)
            or len(ids) not in {2, 4}):
        raise RuntimeError('active foreground lease does not match requested GPU UUIDs')
    pid = receipt.get('pid')
    if not isinstance(pid, int) or pid <= 1:
        raise RuntimeError('lease receipt has no valid controller PID')
    os.kill(pid, 0)
    command = Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ')
    if b'cuda_controller.py' not in command:
        raise RuntimeError('lease owner is not the CUDA foreground controller')
    visible = os.environ.get('CUDA_VISIBLE_DEVICES', '').split(',')
    if len(visible) != len(expected) or not all(visible):
        raise RuntimeError('controller visible-device count does not match the leased GPUs')
    query = subprocess.run(['nvidia-smi', '--query-gpu=uuid,index', '--format=csv,noheader,nounits'],
                           capture_output=True, text=True, timeout=5, check=True)
    by_index = {}
    for line in query.stdout.splitlines():
        fields = [part.strip() for part in line.split(',')]
        if len(fields) != 2: raise RuntimeError('malformed physical GPU identity sample')
        by_index[fields[1]] = fields[0]
    visible_uuids = [token if token.startswith('GPU-') else by_index.get(token) for token in visible]
    if sorted(visible_uuids) != sorted(expected):
        raise RuntimeError('CUDA_VISIBLE_DEVICES does not map to the leased UUID pair')
    return {'path': str(path), 'receipt': receipt, 'visible_devices': visible,
            'visible_gpu_uuids': visible_uuids}


def plan(args: argparse.Namespace) -> dict:
    splits = [args.split_only] if args.split_only else ['layer', 'tensor']
    return {
        'format': 'OBSERVER-MODEL-CALIBRATION-PLAN/1',
        'mode': 'dry_run' if not args.go_real else 'controller_leased',
        'model_path': str(args.model_path), 'gpu_uuids': args.gpu_uuid,
        'lease_gpu_uuids': args.lease_gpu_uuid or args.gpu_uuid,
        'split_only': args.split_only,
        'wall_budget_seconds': args.budget_seconds,
        'visible_answer_tokens': VISIBLE_LIMIT, 'turn_timeout_seconds': TURN_LIMIT,
        'trial_order': [
            {'stage': 'execution', 'context': 32768, 'split': split,
             'batch': batch, 'ubatch': ubatch, 'flash_attention': 'auto',
             'kv_cache': 'f16', 'prompt_target_tokens': 8192, 'reasoning_tokens': 0}
            for split in splits
            for batch, ubatch in ((512, 128), (1024, 256))
        ] + [{'stage': 'context', 'context': n, 'prompt_target_tokens': target_context_tokens(n)}
             for n in (65536, 131072, 262144)]
        + [{'stage': 'thinking', 'reasoning_tokens': n} for n in
           (512, 1024, 2048, 4096, 8192, 16384)],
        'stop_rules': ['15-minute trial budget', 'at least 60 seconds before starting a trial',
                       'at least 1 GiB free per GPU', 'retain only grounded valid JSON'],
    }


class Sampler:
    def __init__(self, uuids: list[str]):
        self.uuids = set(uuids)
        self.rows: list[dict] = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        query = ['nvidia-smi', '--query-gpu=uuid,index,utilization.gpu,memory.used,memory.total',
                 '--format=csv,noheader,nounits']
        while not self.stop.is_set():
            try:
                result = subprocess.run(query, capture_output=True, text=True, timeout=3, check=True)
                for line in result.stdout.splitlines():
                    fields = [part.strip() for part in line.split(',')]
                    if len(fields) != 5 or fields[0] not in self.uuids:
                        continue
                    self.rows.append({'unix': time.time(), 'uuid': fields[0],
                                      'index': int(fields[1]), 'util_percent': int(fields[2]),
                                      'memory_used_mib': int(fields[3]), 'memory_total_mib': int(fields[4])})
            except Exception:
                self.rows.append({'unix': time.time(), 'sample_error': 'nvidia-smi query failed'})
            self.stop.wait(1)

    def start(self): self.thread.start()
    def close(self):
        self.stop.set(); self.thread.join(timeout=4)

    def peaks(self, rows: list[dict] | None = None) -> dict:
        peaks = {}
        for row in self.rows if rows is None else rows:
            if 'uuid' not in row: continue
            peak = peaks.setdefault(row['uuid'], {'peak_util_percent': 0, 'peak_memory_used_mib': 0,
                                                    'memory_total_mib': row['memory_total_mib']})
            peak['peak_util_percent'] = max(peak['peak_util_percent'], row['util_percent'])
            peak['peak_memory_used_mib'] = max(peak['peak_memory_used_mib'], row['memory_used_mib'])
        return peaks


def parse_answer(result: dict) -> tuple[bool, dict]:
    text = result.get('text', '')
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return False, {'valid_json': False}
    answer = str(value.get('answer', '')).replace(' ', '').lower()
    citations = set(value.get('citations', [])) if isinstance(value.get('citations'), list) else set()
    uncertainty = str(value.get('uncertainty', '')).lower()
    grounded = ('elapsed_days=12' in answer and 'north->east' in answer
                and 'direct_transport_proven=false' in answer
                and {'R1', 'R2', 'R3'}.issubset(citations)
                and ('not measured' in uncertainty or 'not prove' in uncertainty
                     or 'cannot' in uncertainty or 'unproven' in uncertainty))
    return grounded, {'valid_json': True, 'grounded_fixture': grounded,
                      'citation_ids': sorted(citations), 'answer_characters': len(text)}


def context_fixture(target_tokens: int) -> list[dict[str, str]]:
    """Place a three-record reasoning needle at dispersed positions in long context."""
    target_chars = max(0, target_tokens * 4 - 1600)
    filler = []
    chars = 0
    index = 0
    while chars < target_chars:
        line = (f'Archive record {index:06d}: routine sample label retained; '
                f'control batch {index % 97:02d}; no route or time inference.\n')
        filler.append(line); chars += len(line); index += 1
    corpus = ''.join(filler)
    third = len(corpus) // 3
    user = (
        'R1: sample Q7 was first observed north at day 4.\n' + corpus[:third] +
        '\nR2: the same tag was observed east at day 11; the report says tags are '
        'consistent with continuity but does not prove it.\n' + corpus[third:2 * third] +
        '\nR3: a later east sample at day 16 was lineage-compatible; direct transport '
        'was not measured.\n' + corpus[2 * third:] +
        '\nCompute elapsed days between first and last sample, state the observed path, '
        'and say whether direct transport is proven. Return answer as '
        'elapsed_days=12,path=north->east,direct_transport_proven=false; cite R1,R2,R3 '
        'and explain the limitation in uncertainty.')
    return [MESSAGES[0], {'role': 'user', 'content': user}]


def target_context_tokens(context_size: int) -> int:
    return {32768: 8192, 65536: 32768, 131072: 65536, 262144: 131072}[context_size]


def run(args: argparse.Namespace, lease: dict, output_path: Path) -> dict:
    if not SKILLS.is_dir(): raise RuntimeError(f'worker source unavailable: {SKILLS}')
    if not args.model_path.is_file(): raise RuntimeError('model path must be an existing file')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(SKILLS))
    from local_worker.servers.llama_cpp import LlamaCppServerAdapter

    adapter = LlamaCppServerAdapter(binary=args.llama_server)
    inspected = adapter.inspect()
    if not inspected.get('available'): raise RuntimeError('llama-server is unavailable')
    binary = Path(inspected['binary']).resolve(strict=True)
    supported = adapter._flags(str(binary))
    required_flags = {'--ctx-size', '--n-gpu-layers', '--split-mode', '--batch-size',
                      '--ubatch-size', '--flash-attn', '--cache-type-k', '--cache-type-v', '--parallel'}
    missing = sorted(required_flags - supported)
    if missing:
        raise RuntimeError(f'llama-server lacks required calibration flags: {missing}')
    model_hash = digest(args.model_path)
    results = []
    started = time.monotonic()
    deadline = started + args.budget_seconds
    slowest_baseline_tps: float | None = None
    sampler = Sampler(args.gpu_uuid)
    sampler.start()
    continuation_answers: dict[str, str] = {}

    source = {'project_commit': subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True,
                  text=True).stdout.strip(), 'adapter_path': str(SKILLS / 'local_worker/servers/llama_cpp.py'),
              'adapter_sha256': digest(SKILLS / 'local_worker/servers/llama_cpp.py'),
              'llama_server': str(binary), 'llama_server_sha256': digest(binary),
              'model_path': str(args.model_path.resolve()), 'model_sha256': model_hash}
    stage = 'execution'

    def checkpoint(status: str = 'running'):
        atomic_json(output_path, {'format': 'OBSERVER-MODEL-CALIBRATION/1', 'status': status,
                    'created_unix': time.time(), 'source': source, 'lease': lease,
                    'wall_budget_seconds': args.budget_seconds,
                    'trial_elapsed_seconds': round(time.monotonic() - started, 3),
                    'current_stage': stage, 'gpu_samples': sampler.rows,
                    'gpu_peaks': sampler.peaks(), 'trials': results})

    checkpoint()

    def remaining(): return deadline - time.monotonic()

    def start_server(settings: dict) -> str:
        trial_id = str(uuid.uuid4())
        log_path = output_path.parent / f'{output_path.stem}-{trial_id}.log'
        profile = {'format': 'CORE4-MODEL-SERVICE/2', 'context_size': settings['context'],
                   'gpu_layers': -1, 'split_mode': settings['split'],
                   'batch_size': settings['batch'], 'ubatch_size': settings['ubatch'],
                   'flash_attention': settings['flash_attention'], 'parallel_slots': 1,
                   'kv_cache_type_k': settings.get('kv_cache', 'f16'),
                   'kv_cache_type_v': settings.get('kv_cache', 'f16'),
                   'allocated_gpu_uuids': args.gpu_uuid, 'startup_timeout_seconds': min(120, max(10, remaining() - TURN_LIMIT)),
                   'log_path': str(log_path),
                   'observer_generation': {'reasoning_tokens': 1, 'preserve_reasoning': False}}
        return adapter.start({'model_path': str(args.model_path), 'repo_root': str(Path.cwd()),
                              'host': '127.0.0.1', 'port': 24000 + (os.getpid() % 15000),
                              'service_profile': profile, 'deadline_epoch': time.time() + max(10, remaining())})

    def trial(settings: dict, reasoning_tokens: int = 0, preserve: bool = False,
              *, server_handle: str | None = None, state_key: str | None = None,
              clear_state: bool = True) -> dict | None:
        if remaining() < TURN_LIMIT: return None
        handle = server_handle
        owns_handle = handle is None
        trial_id = str(uuid.uuid4())
        started_trial = time.monotonic()
        sample_offset = len(sampler.rows)
        row = {'trial_id': trial_id, 'settings': dict(settings), 'reasoning_tokens_cap': reasoning_tokens,
               'preserve_reasoning': preserve}
        try:
            if handle is None: handle = start_server(settings)
            turn_seconds = min(TURN_LIMIT, remaining())
            if turn_seconds < 5: raise TimeoutError('calibration budget exhausted during server startup')
            server = adapter._servers[handle]
            generation = {'reasoning_tokens': max(1, reasoning_tokens), 'preserve_reasoning': preserve}
            if reasoning_tokens and slowest_baseline_tps is not None:
                generation['conservative_tokens_per_second'] = round(0.7 * slowest_baseline_tps, 3)
            adapter.set_observer_generation(handle, generation)
            reasoning_key = state_key or trial_id
            context_target = target_context_tokens(settings['context'])
            messages = context_fixture(context_target)
            if state_key and state_key in continuation_answers:
                messages.insert(1, {'role': 'assistant', 'content': continuation_answers[state_key]})
            row.update({'requested_context_tokens': context_target,
                        'input_bytes': len(json.dumps(messages, ensure_ascii=False).encode('utf-8')),
                        'effective_context_tokens': server.get('effective_context_size', settings['context'])})
            request = {'messages': messages, 'max_tokens': VISIBLE_LIMIT, 'temperature': 0.1,
                       'timeout_seconds': turn_seconds, 'deadline_epoch': time.time() + turn_seconds,
                       'reasoning_mode': 'auto' if reasoning_tokens else 'off',
                       'reasoning_state_key': reasoning_key,
                       'response_format': {'type': 'json_object', 'schema': {
                           'type': 'object', 'properties': {
                               'answer': {'type': 'string'},
                               'citations': {'type': 'array', 'items': {'type': 'string'}},
                               'uncertainty': {'type': 'string'}},
                           'required': ['answer', 'citations', 'uncertainty'],
                           'additionalProperties': False}}}
            response = adapter.run(handle, request)
            if state_key and isinstance(response.get('text'), str):
                continuation_answers[state_key] = response['text']
            good, quality = parse_answer(response)
            usage = response.get('usage', {})
            meta = response.get('response_metadata', {})
            row.update({'ok': response.get('status') == 'succeeded' and good,
                        'quality': quality, 'usage': {k: usage.get(k) for k in
                        ('prompt_tokens', 'completion_tokens', 'reasoning_tokens', 'answer_tokens',
                         'visible_tokens', 'reasoning_ms', 'visible_ms', 'prompt_ms',
                         'prompt_per_second', 'reasoning_prompt_tokens', 'answer_prompt_tokens',
                         'reasoning_prompt_ms', 'answer_prompt_ms', 'reasoning_tokens_per_second',
                         'reasoning_prompt_tokens_per_second', 'answer_prompt_tokens_per_second',
                         'visible_tokens_per_second', 'context_tokens', 'effective_context_size',
                         'reasoning_token_budget') if k in usage},
                        'duration_ms': response.get('duration_ms'),
                        'reasoning_tokens': response.get('reasoning_tokens', usage.get('reasoning_tokens')),
                        'answer_tokens': response.get('answer_tokens', usage.get('answer_tokens')),
                        'response_fields': meta.get('message', {}).get('field_names'),
                        'server_profile': dict(server.get('profile', {})),
                        'server_pid': adapter.describe(handle).get('pid'),
                        'log_path': adapter.describe(handle).get('log_path')})
            rate = usage.get('visible_tokens_per_second')
            if not reasoning_tokens and isinstance(rate, (int, float)) and rate > 0:
                row['native_visible_tokens_per_second'] = round(float(rate), 3)
            row['within_turn_time_limit'] = (response.get('duration_ms') or 1e12) <= TURN_LIMIT * 1000
            row['answer_output_time_ms'] = usage.get('visible_ms')
            row['answer_time_reserve_ok'] = usage.get('visible_ms', 0) <= 45000
            if (usage.get('context_tokens') is not None
                    and usage['context_tokens'] < int(context_target * 0.70)):
                row.update({'ok': False, 'rejection': 'effective_prompt_short_of_context_target'})
            if not row['within_turn_time_limit'] or not row['answer_time_reserve_ok']:
                row.update({'ok': False, 'rejection': 'turn_or_answer_time_reserve_exceeded'})
            peaks = sampler.peaks(sampler.rows[sample_offset:])
            row['gpu_peaks_during_run'] = peaks
            row['gpu_memory_headroom_ok'] = all(
                info['memory_total_mib'] - info['peak_memory_used_mib'] >= PAIR_BYTES_MIN
                for info in peaks.values()) and len(peaks) == 2
            if not row['gpu_memory_headroom_ok']:
                row['ok'] = False; row['rejection'] = 'less_than_1GiB_gpu_headroom_or_missing_sample'
            # Treat model startup errors, CPU offload and CUDA/OOM errors as failed trials.
            log_file = Path(adapter.describe(handle)['log_path']) if handle is not None else None
            if log_file and log_file.exists():
                log = log_file.read_text(encoding='utf-8', errors='replace')[-20000:].lower()
                bad = [x for x in ('out of memory', 'cuda error', 'offloaded to cpu', 'offload to cpu') if x in log]
                if bad: row.update({'ok': False, 'rejection': 'unsupported_or_cpu_offload', 'log_markers': bad})
        except Exception as exc:
            row.update({'ok': False, 'error': f'{type(exc).__name__}: {exc}'})
            try: log_file = Path(adapter.describe(handle)['log_path']) if handle is not None else None
            except Exception: log_file = None
            if log_file and log_file.exists():
                log = log_file.read_text(encoding='utf-8', errors='replace')[-20000:].lower()
                row['log_markers'] = [x for x in ('out of memory', 'cuda error', 'offloaded to cpu', 'offload to cpu') if x in log]
        finally:
            if handle is not None and owns_handle:
                try: adapter.evict(handle)
                except Exception as exc: row['cleanup_error'] = f'{type(exc).__name__}: {exc}'
            elif handle is not None and clear_state:
                try: adapter.clear_reasoning(handle, state_key or trial_id)
                except Exception as exc: row['reasoning_cleanup_error'] = f'{type(exc).__name__}: {exc}'
                if state_key: continuation_answers.pop(state_key, None)
            row['wall_seconds'] = round(time.monotonic() - started_trial, 3)
        results.append(row)
        checkpoint()
        print(json.dumps({'event': 'trial_completed', 'number': len(results), 'stage': stage,
                          'gpu_pair': args.gpu_uuid,
                          'ok': row.get('ok', False), 'duration_ms': row.get('duration_ms'),
                          'reasoning_tokens': row.get('reasoning_tokens')}), flush=True)
        return row

    try:
        # Four one-request trials identify the execution mode with low setup overhead.
        for split in ([args.split_only] if args.split_only else ('layer', 'tensor')):
            for batch, ubatch in ((512, 128), (1024, 256)):
                item = trial({'context': 32768, 'split': split, 'batch': batch,
                              'ubatch': ubatch, 'flash_attention': 'auto', 'kv_cache': 'f16'})
                if item is None: break
        viable = [r for r in results if r.get('ok')]
        viable.sort(key=lambda r: r.get('duration_ms') or 1e12)
        if viable:
            baseline_rates = [r['native_visible_tokens_per_second'] for r in viable
                              if r.get('native_visible_tokens_per_second')]
            if baseline_rates:
                slowest_baseline_tps = min(baseline_rates)
            base = dict(viable[0]['settings'])
            # Grow context in order, then spend remaining budget on thinking caps.
            for context in (65536, 131072, 262144):
                if remaining() < TURN_LIMIT: break
                stage = 'context'
                candidate = {**base, 'context': context}
                item = trial(candidate)
                if item and item.get('ok'): base = candidate
                else: break
            stage = 'thinking'
            if remaining() >= TURN_LIMIT:
                cap_server = start_server(base)
                try:
                    for cap in (512, 1024, 2048, 4096, 8192, 16384):
                        if remaining() < TURN_LIMIT: break
                        previous = next((r for r in reversed(results)
                                         if r.get('reasoning_tokens_cap') and r.get('ok')), None)
                        if cap > 512 and (not previous or not isinstance(previous.get('reasoning_tokens'), int)
                                          or previous['reasoning_tokens'] <= 0
                                          or previous.get('usage', {}).get('reasoning_token_budget',
                                                                            previous['reasoning_tokens']) < previous['reasoning_tokens_cap']):
                            break
                        item = trial(base, reasoning_tokens=cap, server_handle=cap_server)
                        if item and item.get('duration_ms', 999999) > 40000 and cap >= 4096:
                            item['within_answer_time_target'] = False
                            break
                        if item and cap >= 4096 and not item.get('ok'): break
                    useful = next((r for r in reversed(results)
                                   if r.get('reasoning_tokens_cap', 0) >= 4096 and r.get('ok')
                                   and (r.get('reasoning_tokens') or 0) > 0
                                   and r.get('duration_ms', 999999) <= 40000), None)
                    if useful and remaining() >= 4 * TURN_LIMIT:
                        key = str(uuid.uuid4())
                        trial(base, reasoning_tokens=useful['reasoning_tokens_cap'], preserve=False,
                              server_handle=cap_server, state_key=key, clear_state=False)
                        trial(base, reasoning_tokens=useful['reasoning_tokens_cap'], preserve=False,
                              server_handle=cap_server, state_key=key, clear_state=True)
                        trial(base, reasoning_tokens=useful['reasoning_tokens_cap'], preserve=True,
                              server_handle=cap_server, state_key=key, clear_state=False)
                        trial(base, reasoning_tokens=useful['reasoning_tokens_cap'], preserve=True,
                              server_handle=cap_server, state_key=key, clear_state=True)
                finally:
                    try: adapter.evict(cap_server)
                    except Exception as exc: results.append({'ok': False, 'cleanup_error': str(exc),
                                                            'server_pid': adapter.describe(cap_server).get('pid')})
            # Optional cache/flash variants run after reasoning, only with spare wall budget.
            stage = 'optional_memory'
            reference_ms = min((r.get('duration_ms') or 1e12 for r in results
                                if r.get('ok') and r.get('settings') == base), default=1e12)
            for cache_type, flash in (('q8_0', 'auto'), ('f16', 'on')):
                if remaining() < 2 * TURN_LIMIT: break
                candidate = {**base, 'kv_cache': cache_type, 'flash_attention': flash}
                item = trial(candidate)
                if item and item.get('ok') and (item.get('duration_ms') or 1e12) <= 1.05 * reference_ms:
                    base = candidate
    except Exception:
        stage = 'failed'
        checkpoint('failed')
        raise
    finally:
        sampler.close()

    valid = [r for r in results if r.get('ok')]
    best = max(valid, key=lambda r: ((r.get('reasoning_tokens') or 0),
                                     r.get('usage', {}).get('context_tokens', 0),
                                     -(r.get('duration_ms') or 1e12)), default=None)
    return {'format': 'OBSERVER-MODEL-CALIBRATION/1', 'status': 'completed',
            'created_unix': time.time(), 'source': source,
            'lease': lease, 'wall_budget_seconds': args.budget_seconds,
            'trial_elapsed_seconds': round(time.monotonic() - started, 3),
            'gpu_samples': sampler.rows, 'gpu_peaks': sampler.peaks(),
            'trials': results, 'best_valid_trial_id': best.get('trial_id') if best else None,
            'qualification': 'qualified_fixture_only' if best else 'no_valid_configuration'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--gpu-uuid', action='append', default=[])
    parser.add_argument('--lease-gpu-uuid', action='append', default=[],
                        help='full parent-lease UUID set; permits selecting one exact pair')
    parser.add_argument('--split-only', choices=('layer', 'tensor'))
    parser.add_argument('--budget-seconds', type=int, default=DEFAULT_BUDGET)
    parser.add_argument('--llama-server', default='llama-server')
    parser.add_argument('--go-real', action='store_true', help='require an active CUDA foreground lease')
    args = parser.parse_args()
    if len(args.gpu_uuid) != 2 or len(set(args.gpu_uuid)) != 2:
        parser.error('provide exactly two distinct --gpu-uuid values')
    lease_uuids = args.lease_gpu_uuid or args.gpu_uuid
    if len(lease_uuids) not in {2, 4} or len(set(lease_uuids)) != len(lease_uuids):
        parser.error('provide two or four distinct --lease-gpu-uuid values')
    if not set(args.gpu_uuid).issubset(lease_uuids):
        parser.error('selected pair must be contained in the supplied parent lease UUIDs')
    if not 60 <= args.budget_seconds <= DEFAULT_BUDGET:
        parser.error('--budget-seconds must be 60..900')
    if not args.go_real:
        print(json.dumps(plan(args), indent=2)); return 0
    try:
        lease = load_receipt(lease_uuids)
        result = run(args, lease, args.output.resolve())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        print(json.dumps({'output': str(args.output), 'qualification': result['qualification'],
                          'trials': len(result['trials']), 'best_valid_trial_id': result['best_valid_trial_id']}))
        return 0 if result['qualification'] == 'qualified_fixture_only' else 2
    except Exception as exc:
        print(json.dumps({'qualification': 'failed', 'error': f'{type(exc).__name__}: {exc}'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
