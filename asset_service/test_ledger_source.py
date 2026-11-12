"""원장 입력의 추적·경로·YAML 보호 경계 검증."""
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml
from asset_service.ledger_source import AssetLedgerSource, LedgerUniqueLoader


class LedgerSourceBoundaryTests(unittest.TestCase):
    def test_duplicate_and_nonstring_keys_rejected(current_test_case):
        for current_document_text in ('name: first\nname: second\n', '1: value\n', 'root:\n  name: first\n  name: second\n'):
            with current_test_case.assertRaises(ValueError):
                yaml.load(current_document_text, Loader=LedgerUniqueLoader)

    def test_untracked_missing_and_symlink_rejected(current_test_case):
        with tempfile.TemporaryDirectory() as current_directory_name:
            current_repository_root = Path(current_directory_name)
            subprocess.run(['git', 'init', '-q', current_directory_name], check=True)
            current_source_path = current_repository_root / 'ledger.yaml'
            current_source_path.write_text('name: tracked\n')
            with current_test_case.assertRaisesRegex(ValueError, 'Git 추적'):
                AssetLedgerSource(current_repository_root).read_ledger_document('ledger.yaml')
            subprocess.run(['git', '-C', current_directory_name, 'add', 'ledger.yaml'], check=True)
            current_ledger_source = AssetLedgerSource(current_repository_root)
            current_test_case.assertEqual(current_ledger_source.read_ledger_document('ledger.yaml'), {'name': 'tracked'})
            current_source_path.unlink()
            with current_test_case.assertRaises(FileNotFoundError):
                current_ledger_source.read_ledger_document('ledger.yaml')
            current_external_path = current_repository_root / 'outside.yaml'
            current_external_path.write_text('name: outside\n')
            current_source_path.symlink_to(current_external_path)
            with current_test_case.assertRaisesRegex(ValueError, '일반 파일'):
                current_ledger_source.read_ledger_document('ledger.yaml')
