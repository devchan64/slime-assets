-- 기존 콘텐츠 계약의 초기 확장 스키마. 이후 수정은 새 마이그레이션으로 추가한다.


CREATE SCHEMA IF NOT EXISTS content;
CREATE TABLE IF NOT EXISTS content.generations (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    current_generation bigint NOT NULL DEFAULT 0 CHECK(current_generation >= 0),
    snapshot_generation bigint NOT NULL DEFAULT 0,
    CHECK(snapshot_generation BETWEEN 0 AND current_generation)
);
INSERT INTO content.generations(singleton) VALUES(true) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS content.effect_assets (
    asset_id text PRIMARY KEY, document jsonb NOT NULL CHECK(jsonb_typeof(document)='object')
);
CREATE TABLE IF NOT EXISTS content.asset_types (
    asset_type text PRIMARY KEY, display_name text NOT NULL
);
CREATE TABLE IF NOT EXISTS content.image_versions (
    image_id text NOT NULL, image_version text NOT NULL,
    source_path text NOT NULL, image_sha256 text NOT NULL CHECK(image_sha256 ~ '^[0-9a-f]{64}$'),
    image_width integer NOT NULL CHECK(image_width > 0),
    image_height integer NOT NULL CHECK(image_height > 0),
    published_path text NOT NULL,
    PRIMARY KEY(image_id,image_version)
);
CREATE TABLE IF NOT EXISTS content.billboard_assets (
    asset_id text PRIMARY KEY,
    asset_type text NOT NULL REFERENCES content.asset_types(asset_type),
    display_name text NOT NULL,
    image_id text NOT NULL, image_version text NOT NULL,
    asset_revision bigint NOT NULL CHECK(asset_revision > 0),
    retired boolean NOT NULL DEFAULT false,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(metadata)='object'),
    FOREIGN KEY(image_id,image_version) REFERENCES content.image_versions(image_id,image_version)
);
CREATE INDEX IF NOT EXISTS billboard_type_page ON content.billboard_assets(asset_type,asset_id);
CREATE TABLE IF NOT EXISTS content.billboard_properties (
    asset_id text PRIMARY KEY REFERENCES content.billboard_assets(asset_id),
    anchor_x numeric NOT NULL, anchor_y numeric NOT NULL,
    footprint_columns integer NOT NULL CHECK(footprint_columns BETWEEN 1 AND 8),
    footprint_rows integer NOT NULL CHECK(footprint_rows BETWEEN 1 AND 8),
    display_height numeric NOT NULL CHECK(display_height > 0),
    blocks_movement boolean NOT NULL,
    placement_anchor text CHECK(placement_anchor IN ('top','bottom')),
    anchor_mode text CHECK(anchor_mode IN ('tile-bottom-center','tile-intersection')),
    shadow_offset_column numeric NOT NULL,
    shadow_offset_row numeric NOT NULL,
    shadow_scale_column numeric NOT NULL CHECK(shadow_scale_column > 0),
    shadow_scale_row numeric NOT NULL CHECK(shadow_scale_row > 0),
    shadow_coordinate_version integer NOT NULL CHECK(shadow_coordinate_version=2),
    shadow_size_version integer NOT NULL CHECK(shadow_size_version=2)
);
CREATE TABLE IF NOT EXISTS content.change_receipts (
    request_id text PRIMARY KEY, request_sha256 text NOT NULL,
    content_generation bigint NOT NULL UNIQUE,
    changed_by text NOT NULL, changed_at timestamptz NOT NULL DEFAULT now(),
    before_values jsonb NOT NULL, after_values jsonb NOT NULL,
    response_values jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS content.snapshot_runs (
    run_id uuid PRIMARY KEY,
    content_generation bigint NOT NULL,
    state text NOT NULL CHECK(state IN ('pending','running','completed','failed')),
    manifest_sha256 text, payload_sha256 text NOT NULL, error_message text,
    started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
    lease_owner text, lease_expires_at timestamptz
);


CREATE TABLE IF NOT EXISTS content.map_documents (
    document_path text PRIMARY KEY, management_id text NOT NULL UNIQUE,
    document_revision bigint NOT NULL CHECK(document_revision > 0),
    document_metadata jsonb NOT NULL, grid_formats jsonb NOT NULL,
    object_field_present boolean NOT NULL
);
CREATE TABLE IF NOT EXISTS content.map_grid_cells (
    document_path text NOT NULL REFERENCES content.map_documents(document_path),
    grid_field text NOT NULL, tile_column integer NOT NULL CHECK(tile_column>=0),
    tile_row integer NOT NULL CHECK(tile_row>=0), cell_value jsonb NOT NULL,
    PRIMARY KEY(document_path,grid_field,tile_row,tile_column)
);
CREATE TABLE IF NOT EXISTS content.map_object_records (
    document_path text NOT NULL REFERENCES content.map_documents(document_path),
    object_id text NOT NULL, object_order integer NOT NULL CHECK(object_order>=0),
    asset_id text REFERENCES content.billboard_assets(asset_id),
    tile_column integer NOT NULL CHECK(tile_column>=0), tile_row integer NOT NULL CHECK(tile_row>=0),
    object_metadata jsonb NOT NULL,
    PRIMARY KEY(document_path,object_id), UNIQUE(document_path,object_order)
);
CREATE INDEX IF NOT EXISTS map_object_asset_usage ON content.map_object_records(asset_id,document_path);


CREATE TABLE IF NOT EXISTS content.building_properties (
    asset_id text PRIMARY KEY, anchor_x numeric NOT NULL, anchor_y numeric NOT NULL
);
