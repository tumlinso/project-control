from __future__ import annotations
import hashlib, json, subprocess, sys, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from bootstrap import no_symlinks, atomic_new_file, state_root, repository_root, stage

class BootstrapTests(unittest.TestCase):
    def test_atomic_create_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x';atomic_new_file(p,b'one')
            with self.assertRaises(FileExistsError):atomic_new_file(p,b'two')
            self.assertEqual(p.read_bytes(),b'one')

    def test_staging_rejects_symlink_components(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d)/'repo';r.mkdir();other=Path(d)/'other';other.mkdir();(r/'planning').symlink_to(other,target_is_directory=True)
            with self.assertRaises(ValueError):no_symlinks(r,'planning/adaptive-surface-v1/file')

    def test_state_cannot_invalidate_repository(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d)
            with self.assertRaises(ValueError):state_root(repo,'skills',repo/'state')

    def test_repo_must_be_exact_checkout_root(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d);subprocess.run(['git','init','-q',str(repo)],check=True)
            (repo/'nested').mkdir()
            self.assertEqual(repository_root(repo),repo.resolve())
            with self.assertRaises(ValueError):repository_root(repo/'nested')

    def test_stage_is_idempotent_and_does_not_touch_other_files(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d);(repo/'important.txt').write_text('keep')
            a=stage(ROOT,repo);b=stage(ROOT,repo)
            self.assertGreater(a['created_files'],0);self.assertEqual(b['created_files'],0)
            self.assertEqual((repo/'important.txt').read_text(),'keep')

    def test_stage_refuses_different_existing_content(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d);target=repo/'planning/adaptive-surface-v1/README.md';target.parent.mkdir(parents=True);target.write_text('do not overwrite')
            with self.assertRaises(ValueError):stage(ROOT,repo)
            self.assertEqual(target.read_text(),'do not overwrite')

if __name__=='__main__':unittest.main()
