# 개발 에셋 관리 API

이 서비스는 `local/dev` 에셋 DB의 조회·빌보드 속성 및 맵 편집·최초 적재·원장 전달을 담당한다. 운영 환경에서는 기동을 거절한다. 개발 DB 저장이 운영 게임을 직접 갱신하지 않으며 원장 파일을 검토·버전 고정해 배포한다.

## 실행 준비

1. 이 저장소의 Python 환경에 `asset_service/requirements.txt`를 설치한다. 통합 검사는 별도로 `httpx==0.28.1`이 필요하다.
2. [DB 안내](../database/README.md)에 따라 명시적으로 마이그레이션한다. API는 스키마 생성·초기 시드를 자동 실행하지 않는다.
3. DB 관리자가 전용 LOGIN 계정을 만들고 `asset_content_writer` 권한 그룹만 부여한다. 비밀번호는 비밀 관리 도구로 설정한다. 슈퍼유저·CREATEDB·CREATEROLE·REPLICATION·BYPASSRLS 권한이나 content 스키마 CREATE 권한을 주지 않는다. 서비스 시작 시 이를 검사한다.
4. `ASSET_DB_STAGE`, `ASSET_DB_HOST`, `ASSET_DB_PORT`, `ASSET_DB_NAME`, `ASSET_DB_USER`, `ASSET_DB_PASSWORD_FILE`을 설정한다. 비밀 파일은 소유자만 접근 가능한 일반 파일이어야 한다. `SLIME_CONTENT_SERVICE_SECRET_FILE`에 별도의 API 인증 토큰 파일 경로를 설정한다. 비밀번호·토큰을 env 파일에 저장하지 않는다.
5. 에셋 호스트에서 `python -m uvicorn asset_service.api:ASSET_INTERNAL_API_APP --host 127.0.0.1 --port 8791`로 실행한다. 기본 허용 Host는 localhost/127.0.0.1이다. 사설망/VPN 접근은 TLS 프록시와 명시적 `ASSET_SERVICE_ALLOWED_HOSTS` 설정을 통해 제공한다. 와일드카드 Host는 허용하지 않는다.

개발 API의 DB 이름은 stage에 따라 `slime_assets_local` 또는 `slime_assets_dev`다. dev 접속은 TLS 인증서를 검증한다. DB owner 계정을 API 연결에 사용하지 않는다. 게임·Identity 스키마가 있으면 서비스 시작을 거절한다.

## 현재 지원하는 기존 경로

모든 경로에 `Authorization: Bearer <서비스 토큰>` 인증을 적용한다. URL 접두사는 `/internal/v1/content`다.

| 메서드·경로 | 기능 |
|---|---|
| GET `/status` | DB 세대·원장 출력 완료 세대·대기 상태 |
| GET `/types` | 에셋 분류 |
| GET `/billboards?type=...` | 분류별 커서 조회 |
| GET `/billboards/{id}` | 속성·이미지 참조 조회 |
| PATCH `/billboards/{id}` | expectedRevision·requestId 기반 속성 편집 |
| POST `/seed/billboards` | 빌보드 최초 적재·동일 입력 재전송 |
| POST `/seed/bundle` | 빈 DB에 빌보드·맵·선택 이펙트 원자 적재 |
| GET `/effects` | 이펙트 원장 조회 |
| PATCH `/effects/{id}` | 해시 revision·UUID requestId 기반 정렬 편집 |
| POST `/seed/maps` | 맵·건물 앵커 최초 적재·동일 입력 재전송 |
| GET `/map-document?path=...` | 맵·공용 속성 문서 조회 |
| GET `/review-bundle?map=...` | 동일 DB 세대의 맵·이미지·revision 묶음 |
| POST `/map-changes` | expectedGeneration·revision·requestId 기반 원자 저장 |
| POST `/billboard-changes` | 빌보드 속성 일괄 원자 저장 |
| POST `/billboards/{id}/image` | 불변 이미지 버전 등록·속성 변경 |
| GET `/images/{sha256}` | 등록 이미지 경로·규격 조회 |
| POST `/retirement-preview` | revision 기준 폐기 영향 조회 |
| POST `/retirement` | 세대 재확인 후 비활성화·맵 배치 제거 |
| POST `/snapshots/export` | 원장 출력용 일관된 세대와 해시 전달 |
| POST `/snapshots/{runId}/complete` | 출력 성공 기록 |
| POST `/snapshots/{runId}/fail` | 출력 실패 기록 |

`snapshots`는 기존 클라이언트 URL 호환 이름이며 DB 백업 명령이 아니다. API는 파일을 쓰지 않는다. workflow의 기존 `map-assets database-sync`가 payload를 검증하고 에셋 저장소의 원장을 출력한 뒤 완료를 기록한다. 기존 클라이언트의 `SLIME_CONTENT_SERVICE_ORIGIN`을 이 서비스로 연결할 수 있지만 아래 미전환 명령이 필요한 전체 관리 화면의 일괄 전환은 아직 수행하지 않는다.

위 표에 없는 명령은 미지원이며 404를 반환한다. 백업·복구 구현은 보류 상태다. 기존 backend 구현은 전환 전 호환 경로로 남아 있으며 이 서비스가 백엔드 소스를 import하거나 우회 호출하지 않는다. 편집 후 게임 전체의 지형·효과 검증도 아직 이관하지 않았으므로 전체 게임 검증 완료를 의미하는 값을 반환하지 않는다.

## 검증 경계

에셋 속성 입력은 기존 엄격한 타입·필드·범위 계약을 유지한다. 원장 export에서 허용된 맵 경로와 metadata 최상위 필드를 검사하며 몹 스폰 경로·알 수 없는 metadata 필드는 거절한다. 맵 적재·조회·저장은 같은 공개 경로 허용 목록을 사용한다. 변경 저장은 기존 필드 삭제·추가 제한과 교량·지형 이펙트·배치 검증을 유지한다. 맵 최초 적재의 전체 도메인 검증, 중첩 metadata 검증은 후속 작업이다. 검수 조회는 콘텐츠 조회이며 게임 전체 검증 결과를 뜻하지 않는다. 현재 API를 공개 인터넷에 게시하거나 원장 payload를 무검토 자동 배포하지 않는다.

`database.test_migrations`와 같은 격리 테스트 DB를 준비해 `python -m unittest asset_service.test_api.AssetServiceApiTests -v`로 검증한다. 운영·일반 개발 DB에서는 실행하지 않는다. 테스트는 DB 초기화·권한 그룹 재생성을 수행하며 별도 백업을 실행하지 않는다. 실제 별도 호스트·VPN·TLS 배포 검증은 로컬 통합 검사와 구분한다.

통합 최초 적재는 빈 DB에서만 실행한다. 일부 시드가 이미 적재된 DB에는 전체 묶음을 추가 적용하지 않는다. 같은 전체 묶음의 재전송은 최초 응답을 반환하며 후속 편집을 덮어쓰지 않는다. 입력이 달라지면 409로 거절한다. 구성요소 적재와 전체 묶음 영수증은 한 트랜잭션에서 기록하며 응답 최상위 `generation`이 최종 세대다. 구성요소 응답의 세대는 각 적재 단계의 세대다.

일괄 속성 편집은 에셋 ID·요청 ID 중복을 거절하고 한 항목 실패 시 같은 요청의 변경 전체를 롤백한다. 이미지 API는 등록 메타데이터를 저장하며 파일 업로드·이미지 생성은 수행하지 않는다. 같은 이미지 ID·버전의 내용을 바꿀 수 없으며 이전 버전도 보존한다. 폐기는 확인 시점의 세대를 다시 검사하여 빌보드를 비활성화하고 맵 배치와 문서 revision을 함께 변경한다. 이미지 원본은 삭제하지 않는다. 전체 게임 검증은 여전히 별도 전환 대상이다.

초기 적재 경로(seed/bundle, seed/billboards, seed/maps)는 요청 본문 최대 16MiB, 일반 경로는 1MiB다. 프록시도 해당 크기를 수용하도록 설정한다. 공개 맵 루트 문서는 world-map.yaml(표시 배치), battlefield_encounters.yaml(전장 지형 매핑)만 명시적으로 허용한다. 몹 스폰 정의를 허용하는 예외가 아니다.

현재 원장 검증은 관리도구의 기존 시드 생성기로 전달 JSON을 준비한 뒤 `ASSET_TEST_SEED_FILE`에 그 파일의 절대 경로를 지정해 격리 API 검사를 실행한다. 미지정 시 해당 검사는 건너뛰며 일반 fixture 검사만 수행한다. 테스트 자료는 원장 사본이며 공개 저장소에 추가하지 않는다.

실제 HTTP 연결 회귀는 격리 DB 검사에 `ASSET_TEST_WORKFLOW_ROOT`를 명시하여 실행한다. 해당 경로의 `.venv-management/bin/python`과 `tools.review.tests.test_asset_service_http`를 사용하며, 배포 서비스의 런타임 의존성이 아니다. 검사는 임시 루프백 uvicorn과 공용 명령 게이트웨이 처리기를 사용하는 임시 HTTP 서버를 시작해 명령 봉투·CLI 상태 조회·잘못된 토큰 거절을 확인한 뒤 종료한다. GUI 렌더링이나 별도 호스트 TLS 검증은 포함하지 않는다.

`POST /internal/v1/content/snapshots/export?replay=true`는 이미 출력 완료한 현재 세대도 새 실행 ID로 전달한다. 세대 자체는 증가시키지 않으며 실행 중 lease와 공개 경로 검증은 동일하게 적용한다. 개발 원장 동기화의 로컬 영수증 유실을 복구하는 계약이며 과거 DB 백업 복원이 아니다.

원장 출력까지 검증하려면 위 격리 검사에 `ASSET_TEST_LEDGER_SOURCE_ROOT`로 에셋 원본 경로를 추가한다. 테스트는 maps·billboards·effects·등록부를 임시 디렉터리에 복사하고 실제 workflow 출력기와 카탈로그 생성기를 실행한다. 속성 변경 반영·출력 해시·반복 출력 무변경을 검사하며 원본 저장소는 수정하지 않는다. 이는 에셋 사본 검증용 fixture이며 백업 기능이 아니다.
