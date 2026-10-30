"""별도 임시 PostgreSQL에서 마이그레이션의 보호 계약을 검증한다."""
import os
import tempfile
import unittest
from pathlib import Path

import psycopg

from database.migrate import apply_asset_migrations, read_asset_configuration, read_asset_database_secret, ASSET_MIGRATION_ROOT


class AssetDatabaseMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(current_test_class):
        current_test_class.current_database_connection = psycopg.connect(
            host='127.0.0.1', port=int(os.environ['ASSET_TEST_DATABASE_PORT']),
            user='asset_test_owner', dbname='slime_assets_local',
            password=read_asset_database_secret(os.environ['ASSET_TEST_PASSWORD_FILE']), autocommit=True)
        current_identity_record = current_test_class.current_database_connection.execute('SELECT current_user,current_database()').fetchone()
        if current_identity_record != ('asset_test_owner', 'slime_assets_local'):
            current_test_class.current_database_connection.close()
            raise ValueError('격리 테스트 전용 사용자·DB가 아닙니다.')

    @classmethod
    def tearDownClass(current_test_class):
        current_test_class.current_database_connection.close()

    def setUp(current_test_case):
        current_database_connection = current_test_case.current_database_connection
        current_database_connection.execute('DROP SCHEMA IF EXISTS content CASCADE')
        current_database_connection.execute('DROP SCHEMA IF EXISTS asset_admin CASCADE')
        current_database_connection.execute('DROP SCHEMA IF EXISTS game CASCADE')
        current_database_connection.execute('DROP ROLE IF EXISTS asset_content_reader')
        current_database_connection.execute('DROP ROLE IF EXISTS asset_content_writer')

    def test_repeat_preserves_records_and_permissions(current_test_case):
        current_database_connection = current_test_case.current_database_connection
        apply_asset_migrations(current_database_connection)
        current_database_connection.execute("INSERT INTO content.asset_types VALUES('tree','나무')")
        apply_asset_migrations(current_database_connection)
        current_test_case.assertEqual(current_database_connection.execute('SELECT count(*) FROM asset_admin.schema_migrations').fetchone()[0], 2)
        current_test_case.assertEqual(current_database_connection.execute('SELECT display_name FROM content.asset_types').fetchone()[0], '나무')
        with current_database_connection.transaction():
            current_database_connection.execute('SET LOCAL ROLE asset_content_reader')
            current_test_case.assertEqual(current_database_connection.execute('SELECT count(*) FROM content.asset_types').fetchone()[0], 1)
        for current_role_name, current_forbidden_sql in (
            ('asset_content_reader', "INSERT INTO content.asset_types VALUES('rock','바위')"),
            ('asset_content_reader', 'SELECT * FROM content.change_receipts'),
            ('asset_content_writer', 'CREATE TABLE content.forbidden_table(id int)'),
            ('asset_content_writer', 'DELETE FROM content.image_versions'),
            ('asset_content_writer', 'DELETE FROM content.change_receipts')):
            with current_test_case.assertRaises(psycopg.errors.InsufficientPrivilege):
                with current_database_connection.transaction():
                    current_database_connection.execute('SET LOCAL ROLE ' + current_role_name)
                    current_database_connection.execute(current_forbidden_sql)
        current_test_case.assertIsNone(current_database_connection.execute("SELECT to_regnamespace('game')").fetchone()[0])

    def test_game_schema_rejected_before_changes(current_test_case):
        current_database_connection = current_test_case.current_database_connection
        current_database_connection.execute('CREATE SCHEMA game')
        with current_test_case.assertRaises(ValueError):
            apply_asset_migrations(current_database_connection)
        current_test_case.assertIsNone(current_database_connection.execute("SELECT to_regnamespace('asset_admin')").fetchone()[0])

    def test_untracked_content_rejected(current_test_case):
        current_test_case.current_database_connection.execute('CREATE SCHEMA content')
        with current_test_case.assertRaises(ValueError):
            apply_asset_migrations(current_test_case.current_database_connection)

    def test_changed_hash_rejected(current_test_case):
        current_database_connection = current_test_case.current_database_connection
        apply_asset_migrations(current_database_connection)
        current_database_connection.execute("UPDATE asset_admin.schema_migrations SET migration_sha256='invalid'")
        with current_test_case.assertRaises(ValueError):
            apply_asset_migrations(current_database_connection)

    def test_missing_history_entry_rejected(current_test_case):
        current_database_connection = current_test_case.current_database_connection
        apply_asset_migrations(current_database_connection)
        current_database_connection.execute("DELETE FROM asset_admin.schema_migrations WHERE migration_name='001_content_schema.sql'")
        with current_test_case.assertRaises(ValueError):
            apply_asset_migrations(current_database_connection)

    def test_failed_migration_rolls_back_all_changes(current_test_case):
        with tempfile.TemporaryDirectory() as current_directory_name:
            current_migration_root = Path(current_directory_name)
            for current_migration_file in ASSET_MIGRATION_ROOT.glob('*.sql'):
                (current_migration_root / current_migration_file.name).write_bytes(current_migration_file.read_bytes())
            (current_migration_root / '003_invalid_schema.sql').write_text('SELECT nonexistent_column FROM content.asset_types;')
            with current_test_case.assertRaises(psycopg.errors.UndefinedColumn):
                apply_asset_migrations(current_test_case.current_database_connection, current_migration_root)
        current_test_case.assertIsNone(current_test_case.current_database_connection.execute("SELECT to_regnamespace('content')").fetchone()[0])
        current_test_case.assertIsNone(current_test_case.current_database_connection.execute("SELECT to_regnamespace('asset_admin')").fetchone()[0])

    def test_local_configuration_rejects_game_or_remote(current_test_case):
        current_environment_values = dict(ASSET_DB_HOST='127.0.0.1', ASSET_DB_PORT='25432', ASSET_DB_NAME='slime_assets_local', ASSET_DB_USER='owner', ASSET_DB_PASSWORD_FILE='/unused')
        current_test_case.assertEqual(read_asset_configuration(current_environment_values)['SSLMODE'], 'disable')
        for current_invalid_settings in ({'ASSET_DB_NAME': 'slime'}, {'ASSET_DB_HOST': 'production.example'}, {'ASSET_DB_PORT': '0'}, {'ASSET_DB_STAGE': 'unknown'}):
            with current_test_case.assertRaises(ValueError):
                read_asset_configuration(current_environment_values | current_invalid_settings)


if __name__ == '__main__':
    unittest.main()
