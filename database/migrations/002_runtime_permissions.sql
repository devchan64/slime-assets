-- 비밀번호 없는 권한 그룹. 로그인 계정 생성·비밀값 주입은 배포 단계에서 별도로 수행한다.
CREATE ROLE asset_content_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE asset_content_writer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA content TO asset_content_reader, asset_content_writer;
GRANT SELECT ON content.generations, content.effect_assets, content.asset_types,
    content.image_versions, content.billboard_assets, content.billboard_properties,
    content.map_documents, content.map_grid_cells, content.map_object_records,
    content.building_properties TO asset_content_reader, asset_content_writer;
GRANT INSERT, UPDATE, DELETE ON content.effect_assets, content.asset_types,
    content.billboard_assets, content.billboard_properties, content.map_documents,
    content.map_grid_cells, content.map_object_records, content.building_properties
    TO asset_content_writer;
GRANT UPDATE ON content.generations TO asset_content_writer;
GRANT INSERT ON content.image_versions TO asset_content_writer;
GRANT SELECT, INSERT ON content.change_receipts TO asset_content_writer;
GRANT SELECT, INSERT, UPDATE ON content.snapshot_runs TO asset_content_writer;
