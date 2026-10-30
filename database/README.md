# 에셋 DB 로컬 실행

`docker-compose.database.yml`은 에셋 호스트 전용 PostgreSQL 로컬 실행 구성이다. 게임 DB와 다른 호스트에서 실행한다. 운영 환경 배포 구성으로 사용하지 않는다.

## 준비

- 에셋 호스트에서 `.local/data/asset-postgres`와 `.local/secrets` 디렉터리를 준비한다.
- `.local/secrets/asset_database_password`에 비어 있지 않은 비밀번호를 보안 도구로 저장한다. 파일 권한은 소유자 읽기·쓰기만 허용한다. 비밀번호를 명령 인자·env 파일·Git·로그에 넣지 않는다.
- 기존 PostgreSQL 데이터 폴더를 연결하지 않는다. 최초 초기화 대상은 에셋 전용 빈 디렉터리다.
- 비밀 파일과 데이터 디렉터리가 없으면 기동을 실패시키며 다른 DB를 사용하지 않는다.

## 명령

```bash
docker compose -f docker-compose.database.yml config --quiet
docker compose -f docker-compose.database.yml up -d --wait asset-db
docker compose -f docker-compose.database.yml ps
docker compose -f docker-compose.database.yml stop asset-db
```

프로젝트 이름은 `slime-assets-local`, DB 이름은 `slime_assets_local`, 호스트 접속은 `127.0.0.1:25432`다. 다른 호스트에는 DB 포트를 공개하지 않는다. 관리 API 연결은 별도 구현한다. 데이터는 `.local/data/asset-postgres`에 영속화한다. `POSTGRES_USER`는 초기화 소유자이며 향후 API 런타임 계정으로 사용하지 않는다.

## 현재 구현 범위

독립 DB 실행 구성과 명시적 마이그레이션을 제공한다. [개발 API](../asset_service/README.md)는 조회·빌보드 속성 편집·원장 전달부터 구현했다. 데이터 이전·나머지 API 전환·런타임 로그인 계정 배포는 아직 수행하지 않았다. 백업 구현은 보류한다. 기존 에셋 등록부와 게임 DB를 자동 변경하지 않는다. 컨테이너를 기동했다고 데이터 분리 완료로 간주하지 않는다.

## 명시적 마이그레이션

이 저장소의 Python 환경에 `database/requirements.txt`를 설치한다. 다른 저장소의 Python 소스나 데이터 파일을 import하지 않는다.

```bash
ASSET_DB_STAGE=local ASSET_DB_HOST=127.0.0.1 ASSET_DB_PORT=25432 \
ASSET_DB_NAME=slime_assets_local ASSET_DB_USER=asset_database_owner \
ASSET_DB_PASSWORD_FILE="$PWD/.local/secrets/asset_database_password" \
python -m database.migrate > .local/asset-database-migrate.log 2>&1
```

실패 시 종료 코드를 보존하고 `.local/asset-database-migrate.log`의 마지막 traceback을 확인한다. 각 SQL 문은 최대 5초, 잠금 대기도 최대 5초로 제한하며 단계 로그를 기록한다. dev/prod는 명시적인 stage·전용 DB 이름과 인증서 검증 TLS 연결을 요구한다. 이 로컬 Compose로 dev/prod를 기동하지 않는다.

마이그레이션은 전용 DB 이름·게임/Identity 스키마 부재를 확인하고 트랜잭션 잠금 아래 순서대로 실행한다. 기존 content 스키마를 이력 없이 자동 인수하지 않는다. 실행 이력과 SQL SHA-256이 다르면 실패하며 적용된 파일을 수정하지 않고 다음 번호 SQL을 추가한다. DDL·이력은 하나의 트랜잭션으로 커밋한다.

`001`은 이미지·빌보드·공용 속성·맵·건물·변경 기록의 기존 content 계약을 보존한 초기 스키마다. `002`는 비밀번호 없는 `asset_content_reader`·`asset_content_writer` 권한 그룹을 만든다. 조회 그룹은 감사 기록을 읽지 못하며 쓰기 그룹도 스키마 생성·이미지 버전 수정/삭제·감사 기록 수정/삭제를 할 수 없다. 클러스터에 동명 역할이 이미 존재하면 자동 재사용하지 않고 실패한다. 로그인 계정·서비스 인증은 배포 단계에서 별도로 구성한다.

## 격리 통합 검사

운영 DB가 아닌 임시 PostgreSQL 클러스터에 사용자 `asset_test_owner`, DB `slime_assets_local`을 준비한다. 소유자 전용 비밀 파일과 루프백 임시 포트를 `ASSET_TEST_PASSWORD_FILE`, `ASSET_TEST_DATABASE_PORT`로 전달하고 `python -m unittest database.test_migrations -v`를 실행한다. 이 검사는 테스트 DB의 content·asset_admin·game 스키마와 테스트 권한 그룹을 초기화하므로 일반 개발 DB에도 실행하지 않는다.

## 백업 범위

백업 구현은 보류 상태다. 테스트 준비·실행·정리에서 별도 백업을 실행하지 않는다. 임시 DB의 트랜잭션 롤백 검증과 테스트 컨테이너 정리는 계속 수행한다.
