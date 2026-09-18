import contextlib
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import codex_mux


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'mux'
        self.source = Path(self.temp.name) / 'original'
        self.source.mkdir()
        registry = codex_mux.default_registry()
        registry['accounts'] = [{'name': 'acc1', 'codexHome': 'accounts/acc1'}]
        registry['defaultAccount'] = 'acc1'
        codex_mux.save_registry(registry, self.root)
        self.state = self.root / 'shared'

    def transcript(self, home, name='one', directory='sessions', text='hello'):
        path = home / directory / '2026' / f'rollout-{name}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'type': 'session_meta', 'payload': {
            'id': name, 'cwd': '/original/project'}}) + '\n' +
            json.dumps({'type': 'event_msg', 'payload': {'text': text}}) + '\n')
        return path

    def run_import(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return codex_mux.as_main(['migrate-sessions', '--from', str(self.source),
                                     *args], self.root)

    def test_preview_does_not_create_shared_state(self):
        self.transcript(self.source)
        self.assertEqual(self.run_import(), 0)
        self.assertFalse(self.state.exists())

    def test_copy_preserves_original_and_skips_credentials_and_database(self):
        source = self.transcript(self.source)
        archive = self.transcript(self.source, 'two', 'archived_sessions')
        for name in ('auth.json', 'config.toml', 'state_5.sqlite', 'history.jsonl'):
            (self.source / name).write_text('private')
        before = source.read_bytes()
        self.run_import('--apply', '--no-reindex')
        for path in (source, archive):
            target = self.state / path.relative_to(self.source)
            self.assertEqual(target.read_bytes(), path.read_bytes())
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertNotEqual(target.stat().st_ino, path.stat().st_ino)
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse((self.state / 'auth.json').exists())
        self.assertFalse((self.state / 'state_5.sqlite').exists())
        self.run_import('--apply', '--no-reindex')
        self.assertEqual(len(list(self.state.rglob('*.jsonl'))), 2)

    def test_conflicting_id_at_different_path_blocks_all_copies(self):
        self.transcript(self.source, 'new')
        self.transcript(self.source, 'one')
        target = self.transcript(self.state, 'one', 'archived_sessions', 'different')
        before = target.read_bytes()
        with self.assertRaises(codex_mux.MuxError):
            self.run_import('--apply', '--no-reindex')
        self.assertEqual(target.read_bytes(), before)
        self.assertFalse((self.state / 'sessions').exists())

    def test_incomplete_transcript_rejected(self):
        source = self.transcript(self.source)
        source.write_bytes(source.read_bytes()[:-1])
        with self.assertRaises(codex_mux.MuxError):
            self.run_import('--apply', '--no-reindex')
        self.assertFalse(self.state.exists())

    def test_symlink_transcript_rejected(self):
        source = self.transcript(self.source)
        source.with_name('link.jsonl').symlink_to(source)
        with self.assertRaises(codex_mux.MuxError):
            self.run_import()

    def test_source_changes_during_copy_publish_nothing(self):
        source = self.transcript(self.source)
        original_copy = codex_mux.shutil.copyfileobj

        def mutate(src, dst):
            original_copy(src, dst)
            with source.open('a') as handle:
                handle.write('{"type":"event_msg"}\n')

        with mock.patch.object(codex_mux.shutil, 'copyfileobj', side_effect=mutate):
            with self.assertRaises(codex_mux.MuxError):
                self.run_import('--apply', '--no-reindex')
        self.assertEqual(list(self.state.rglob('*.jsonl')), [])

    def test_reindex_runs_after_copy(self):
        self.transcript(self.source)
        home = self.root / 'accounts/acc1'
        home.mkdir(parents=True)
        (home / 'auth.json').write_text('{}')
        with mock.patch.object(codex_mux, 'reindex_command', return_value=0) as reindex:
            self.run_import('--apply', '--reindex', '--account', 'acc1')
        reindex.assert_called_once_with(['acc1'], self.root)

    def test_archives_shared_by_every_account(self):
        registry = codex_mux.load_registry(root=self.root)
        codex_mux.ensure_layout(registry, root=self.root)
        self.assertEqual((self.root / 'accounts/acc1/archived_sessions').resolve(),
                         self.state / 'archived_sessions')

    def test_already_shared_home_is_noop(self):
        registry = codex_mux.load_registry(root=self.root)
        registry['sharedState'] = str(self.source)
        codex_mux.save_registry(registry, self.root)
        with mock.patch.object(codex_mux, 'reindex_command') as reindex:
            self.assertEqual(self.run_import('--apply'), 0)
            reindex.assert_not_called()

    def test_nested_source_rejected(self):
        self.source = self.state / 'old-home'
        self.source.mkdir(parents=True)
        with self.assertRaises(codex_mux.MuxError):
            self.run_import('--apply', '--no-reindex')

    def test_reindex_failure_leaves_copies_for_retry(self):
        self.transcript(self.source)
        home = self.root / 'accounts/acc1'
        home.mkdir(parents=True)
        (home / 'auth.json').write_text('{}')
        with mock.patch.object(codex_mux, 'reindex_command',
                               side_effect=codex_mux.MuxError('offline')):
            with self.assertRaisesRegex(codex_mux.MuxError, 'sessions copied'):
                self.run_import('--apply', '--reindex')
        self.assertEqual(len(list(self.state.rglob('*.jsonl'))), 1)

    def test_concurrent_destination_creation_never_overwrites(self):
        source = self.transcript(self.source)
        target = self.state / source.relative_to(self.source)
        original_link = codex_mux.os.link

        def race(src, dst):
            Path(dst).write_text('concurrent writer')
            original_link(src, dst)

        with mock.patch.object(codex_mux.os, 'link', side_effect=race):
            with self.assertRaises(codex_mux.MuxError):
                self.run_import('--apply', '--no-reindex')
        self.assertEqual(target.read_text(), 'concurrent writer')

    def snapshot(self, home):
        return {str(p.relative_to(home)): (p.read_bytes(), p.stat().st_mtime_ns,
                                          stat.S_IMODE(p.stat().st_mode), p.stat().st_ino)
                for p in home.rglob('*') if p.is_file()}

    def test_default_apply_is_offline_and_preserves_all_original_files(self):
        self.transcript(self.source)
        self.transcript(self.state, 'existing')
        for home in (self.source, self.state):
            for name in ('auth.json', 'config.toml', 'state_5.sqlite', 'state_5.sqlite-wal'):
                (home / name).write_text('untouched')
        before_source = self.snapshot(self.source)
        before_state = self.snapshot(self.state)
        with mock.patch.object(codex_mux, 'AppServerClient',
                               side_effect=AssertionError('must stay offline')):
            with mock.patch.object(codex_mux, 'ensure_layout',
                                   side_effect=AssertionError('must not alter layout')):
                self.run_import('--apply')
        self.assertEqual(self.snapshot(self.source), before_source)
        after = self.snapshot(self.state)
        self.assertEqual({key: after[key] for key in before_state}, before_state)
        # Later edits to an imported copy cannot alter the original inode.
        (self.state / 'sessions/2026/rollout-one.jsonl').write_text('continued')
        self.assertEqual(self.snapshot(self.source), before_source)

    def test_copy_io_failure_preserves_source_and_destination(self):
        self.transcript(self.source)
        self.transcript(self.state, 'existing')
        before_source = self.snapshot(self.source)
        before_state = self.snapshot(self.state)
        with mock.patch.object(codex_mux.shutil, 'copyfileobj', side_effect=OSError('disk full')):
            with self.assertRaises(codex_mux.MuxError):
                self.run_import('--apply')
        self.assertEqual(self.snapshot(self.source), before_source)
        self.assertEqual(self.snapshot(self.state), before_state)
        self.assertFalse(list(self.state.glob('.session-import-*')))

    def test_interrupted_publication_can_be_retried_without_data_loss(self):
        self.transcript(self.source, 'one')
        self.transcript(self.source, 'two')
        before = self.snapshot(self.source)
        original_link = codex_mux.os.link
        calls = 0

        def interrupt(src, dst):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt()
            original_link(src, dst)

        with mock.patch.object(codex_mux.os, 'link', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_import('--apply')
        self.assertEqual(len(list(self.state.rglob('*.jsonl'))), 1)
        self.run_import('--apply')
        self.assertEqual(len(list(self.state.rglob('*.jsonl'))), 2)
        self.assertEqual(self.snapshot(self.source), before)

    def test_destination_symlink_rejected_before_writing_into_source(self):
        self.transcript(self.source)
        self.state.mkdir()
        (self.state / 'sessions').symlink_to(self.source / 'sessions')
        before = self.snapshot(self.source)
        with self.assertRaisesRegex(codex_mux.MuxError, 'symlink destination'):
            self.run_import('--apply')
        self.assertEqual(self.snapshot(self.source), before)

    def test_nested_source_symlink_not_silently_skipped(self):
        path = self.transcript(self.source)
        (path.parent / 'linked').symlink_to(path.parent, target_is_directory=True)
        with self.assertRaisesRegex(codex_mux.MuxError, 'nested session symlink'):
            self.run_import('--apply')

    def discover(self, home, environment):
        registry = codex_mux.load_registry(root=self.root)
        with mock.patch.object(Path, 'home', return_value=home):
            with mock.patch.dict(codex_mux.os.environ, environment, clear=True):
                with contextlib.redirect_stdout(io.StringIO()):
                    return codex_mux.discover_session_source(registry, self.root)

    def test_discovery_uses_current_users_home(self):
        user_home = self.source / 'different-user'
        self.transcript(user_home / '.codex')
        self.assertEqual(self.discover(user_home, {}), user_home / '.codex')
        self.assertFalse(self.state.exists())

    def test_discovery_finds_custom_environment_home(self):
        self.transcript(self.source)
        self.assertEqual(self.discover(self.source / 'user', {'CODEX_HOME': str(self.source)}),
                         self.source)

    def test_discovery_deduplicates_symlink_alias(self):
        user_home = self.source / 'user'
        default = user_home / '.codex'
        self.transcript(default)
        alias = self.source / 'alias'
        alias.symlink_to(default)
        self.assertEqual(self.discover(user_home, {'CODEX_HOME': str(alias)}), default)

    def test_discovery_requires_choice_for_multiple_original_homes(self):
        user_home = self.source / 'user'
        self.transcript(user_home / '.codex')
        other = self.source / 'custom'
        self.transcript(other, 'two')
        with self.assertRaisesRegex(codex_mux.MuxError, 'multiple original homes'):
            self.discover(user_home, {'CODEX_HOME': str(other)})
        self.assertFalse(self.state.exists())

    def test_discovery_does_not_select_managed_environment_home(self):
        user_home = self.source / 'user'
        self.transcript(user_home / '.codex')
        managed = self.root / 'accounts/acc1'
        self.transcript(managed, 'two')
        self.assertEqual(self.discover(user_home, {'CODEX_HOME': str(managed)}),
                         user_home / '.codex')

    def test_discovery_missing_custom_home_requires_explicit_path(self):
        with self.assertRaisesRegex(codex_mux.MuxError, 'no original chats found'):
            self.discover(self.source, {})
        self.assertFalse(self.state.exists())

    def test_discovery_invalid_candidate_does_not_silently_choose_another(self):
        user_home = self.source / 'user'
        bad = self.transcript(user_home / '.codex')
        bad.write_text('unfinished')
        other = self.source / 'custom'
        self.transcript(other)
        with self.assertRaisesRegex(codex_mux.MuxError, 'could not be validated'):
            self.discover(user_home, {'CODEX_HOME': str(other)})

    def test_discovery_recognizes_already_shared_default_home(self):
        user_home = self.source / 'user'
        default = user_home / '.codex'
        self.transcript(default)
        registry = codex_mux.load_registry(root=self.root)
        registry['sharedState'] = str(default)
        codex_mux.save_registry(registry, self.root)
        self.assertEqual(self.discover(user_home, {}), default)

    def test_explicit_source_bypasses_automatic_discovery(self):
        self.transcript(self.source)
        with mock.patch.object(codex_mux, 'discover_session_source',
                               side_effect=AssertionError('explicit path must win')):
            self.run_import()
        self.assertFalse(self.state.exists())
