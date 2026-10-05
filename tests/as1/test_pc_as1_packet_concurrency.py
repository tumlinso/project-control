"""Connection lifecycle regression with real SQLite and a bounded child process."""
import subprocess
import sys
import textwrap


def test_packet_connection_lifecycle_preserves_concurrent_operations(tmp_path):
    # A deadlock must fail within the outer timeout, including executor shutdown.
    code = textwrap.dedent('''
        from concurrent.futures import ThreadPoolExecutor
        import faulthandler
        import sqlite3
        import sys
        import threading
        import time
        from project_control.as1_packets import SQLitePacketStore

        faulthandler.dump_traceback_later(20, exit=True)
        original_connect = sqlite3.connect
        monitor = threading.Lock()
        active = 0
        overlaps = []
        journal_updates = []

        def lifecycle(action, operation):
            global active
            with monitor:
                active += 1
                if active > 1:
                    overlaps.append(action)
            try:
                # Widen the actual open/close race deterministically.
                time.sleep(.001)
                return operation()
            finally:
                with monitor:
                    active -= 1

        class Connection(sqlite3.Connection):
            def close(self):
                return lifecycle('close', super().close)

            def execute(self, sql, *args, **kwargs):
                if sql.upper().startswith('PRAGMA JOURNAL_MODE='):
                    journal_updates.append(sql)
                return super().execute(sql, *args, **kwargs)

        def connect(*args, **kwargs):
            kwargs['factory'] = Connection
            return lifecycle('connect', lambda: original_connect(*args, **kwargs))

        sqlite3.connect = connect
        scope = {'principal': 'alice', 'project': 'pc', 'profile': 'observer'}
        stores = [SQLitePacketStore(sys.argv[1], recent_terminal_limit=4) for _ in range(3)]
        assert journal_updates == ['PRAGMA journal_mode=WAL']
        journal_updates.clear()
        seed = stores[0].create(tool='seed', payload={'seed': True}, access_scope=scope)
        expired = stores[0].create(tool='seed', payload={'expired': True}, access_scope=scope, ttl_seconds=0)
        barrier = threading.Barrier(7)

        def write(index):
            store = stores[index]
            barrier.wait(timeout=5)
            packets = []
            for step in range(20):
                packet = store.create(tool='write', payload={'writer': index, 'step': step}, access_scope=scope)
                owner = f'job-{index}-{step}'
                store.retain_job(owner, [packet.packet_id], terminal=bool(step % 2))
                assert store.lookup(packet.alias, access_scope=scope).packet.payload == packet.payload
                assert store.lookup(packet.packet_id, access_scope={'principal': 'bob'}).status == 'forbidden'
                store.unpin(owner)
                packets.append(packet)
            return packets

        def read(index):
            store = stores[index]
            barrier.wait(timeout=5)
            for _ in range(40):
                assert store.lookup(seed.alias, access_scope=scope).packet.payload == {'seed': True}
                assert store.invocation(seed.packet_id, access_scope=scope) is not None
            return []

        def collect():
            barrier.wait(timeout=5)
            for _ in range(30):
                stores[0].gc()
            return []

        with ThreadPoolExecutor(max_workers=7) as pool:
            futures = [pool.submit(write, i) for i in range(3)]
            futures += [pool.submit(read, i) for i in range(3)]
            futures.append(pool.submit(collect))
            packets = [packet for future in futures for packet in future.result(timeout=15)]
        assert len(packets) == 60
        assert len({packet.alias for packet in packets}) == 60
        assert not overlaps, overlaps
        assert not journal_updates, journal_updates
        assert stores[0].lookup(expired.alias, access_scope=scope).status == 'expired'
        for packet in packets:
            assert stores[1].lookup(packet.alias, access_scope=scope).packet.payload == packet.payload
        with stores[0]._db() as db:
            assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        faulthandler.cancel_dump_traceback_later()
        print('60 packets; concurrent write/read/retain/lookup/gc; no lifecycle overlap; integrity ok')
    ''')
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'no lifecycle overlap; integrity ok' in result.stdout
