import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('cuda_metadata_stage', Path(__file__).with_name('generate_stage.py'))
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


class StageTests(unittest.TestCase):
    def test_architecture_is_path_bound(self):
        self.assertEqual(stage.architecture_tags('references/generic.md'), [])
        self.assertEqual(stage.architecture_tags('references/v100_programming_guide.md'), ['volta', 'v100'])
        self.assertEqual(stage.architecture_tags('references/architectures/hopper/router.md'), ['hopper'])
        self.assertEqual(stage.architecture_tags('references/systems/native.md'), [])

    def test_links_are_local_bounded_and_literal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            known = {'references/a.md', 'references/b.md', 'SKILL.md'}
            text = ('[relative](b.md#section) [root](references/b.md) '
                    '[external](https://example.com/references/b.md) '
                    '[outside](../../secret.md) ![image](../SKILL.md) '
                    '`references/b.md` [missing](missing.md)')
            self.assertEqual(stage.local_links(root, 'references/a.md', text, known), {'references/b.md'})

    def test_retrieval_edit_preserves_all_other_bytes(self):
        original = ('controller inspect run background\n' + stage.OLD_GUIDE + stage.OLD_TEXT + '\nGPU interlocks\n').encode()
        changed = stage.stage_skill(original)
        self.assertIn(b'skill="auto"', changed)
        self.assertNotIn(b'skill="cuda"', changed)
        restored = changed.decode().replace(stage.NEW_TEXT, stage.OLD_GUIDE + stage.OLD_TEXT).encode()
        self.assertEqual(restored, original)
        with self.assertRaises(ValueError):
            stage.stage_skill(b'changed preimage')

    def test_stable_id_is_full_path_not_basename(self):
        self.assertNotEqual(stage.resource_id('references/architectures/volta/router.md'),
                            stage.resource_id('references/architectures/hopper/router.md'))
        self.assertEqual(stage.resource_id('references/a.md'), stage.resource_id('references/a.md'))

    def test_backtick_routes_require_unambiguous_existing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            known = {'references/a.md', 'references/b.md', 'b.md', 'references/c.md'}
            targets, skipped = stage.backtick_references(root, 'references/a.md',
                '`references/c.md` `b.md` `missing.md` `../../secret.md` `<repo>/custom.md` `script --help`', known)
            self.assertEqual(targets, {'references/c.md'})
            self.assertEqual({r['reason'] for r in skipped}, {'ambiguous', 'unresolved_or_outside_NONatlas', 'not_a_local_literal_path'})

    def test_manifest_skill_identity_uses_staged_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'assets').mkdir()
            (root / 'assets/cuda-markdown-manifest.json').write_text('{}')
            (root / 'SKILL.md').write_text('# CUDA\n' + stage.OLD_GUIDE + stage.OLD_TEXT)
            corpus, skill, receipt = stage.build(root)
            row = next(r for r in corpus['resources'] if r['path']=='SKILL.md')
            self.assertEqual(row['sha256'], stage.digest(skill))
            self.assertNotEqual(row['sha256'], stage.digest((root/'SKILL.md').read_bytes()))
            self.assertTrue(next(r for r in corpus['resources'] if r['path']==stage.GENERATED)['index_excluded'])


if __name__ == '__main__':
    unittest.main()
