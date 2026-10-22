# 빌보드 카탈로그 사용법

`catalog.yaml`은 맵 제작 시 에셋 선택과 배치 판단에 사용하는 주요 참고 자료다. 등록된 모든 PNG와 이전 버전을 포함한다. 카탈로그의 용도 설명은 배치 권장사항이며 지역의 세계관이나 실제 게임 통행 판정을 대체하지 않는다.

## 에셋 선택 순서

1. `family`, `modeling.purpose`, `recommended_uses`로 대상 장소와 역할을 선택한다.
2. `source_file.path`의 실제 이미지를 열어 형태·색·실루엣을 확인한다. `source_file.width/height`는 원본 픽셀 해상도이며 게임 표시 크기가 아니다.
3. `placement.status`가 `공용 속성 등록됨`인지 확인한다. `설정 필요`인 구버전·미등록 항목은 크기와 앵커를 추정해서 사용하지 않는다.
4. `displayHeight`와 `outdoorDisplayHeight`로 크기를 비교한다. 일반 큰 나무의 공용 높이는 300이며, 야외필드 0.5배에서는 150이다. 별도 소형·대형 변형은 각자의 값을 따른다. 화면 줌은 별도다.
5. 접지 열·행 수, `occupied_tile_count`, `billboardPlacementAnchor`, `movement`를 확인한다. 2×2 중심 접지는 반 칸을 포함하여 실제 검수에서는 3×3 셀에 걸친다. 접지 고도를 맞추고 다른 점유 영역과 겹치지 않도록 한다.
6. `modeling.composition`과 `avoid`에 따라 군락·빈터·주요 동선을 구성한다. `map_references`는 기존 사용 예이며 해당 장소에만 쓸 수 있다는 제한이 아니다.

## 정보의 소유권

- 이미지 경로·해시·원본 크기: `asset-registry.yaml`.
- 표시 높이·접지·앵커·통행 차단: `assets/maps/sprite-display-properties.yaml`.
- 에셋별 용도·배치 지침: 이 카탈로그의 `modeling`. 실제 검수 결과에 따라 수동으로 구체화한다. 자동 초안의 `basis`는 등록 명칭·계열 기반임을 표시하며 이미지 육안 검수 완료를 뜻하지 않는다.
- 사용 맵: `assets/maps/`의 실제 `textureKey` 참조.

`source_file`, `placement`, `map_references`는 위 원본에서 파생된다. 카탈로그에서 숫자만 고치면 게임 기본값은 바뀌지 않는다. 먼저 공용 원본과 등록·잠금을 갱신한 뒤 카탈로그를 동기화한다. 동기화는 기존 `modeling` 설명을 덮어쓰지 않는다.

## 갱신·검증

slime-workflow 루트에서 실행한다.

```bash
PYTHONPATH=. .venv-management/bin/python tools/review/maintenance/sync_billboard_catalog.py --write
PYTHONPATH=. .venv-management/bin/python tools/review/maintenance/sync_billboard_catalog.py --check
```

신규 등록·공용 크기 변경·맵 재배치 후 실행한다. 검증은 원본 이미지 존재·SHA-256, 중복 키·ID, 공용 속성 계약, 카탈로그 최신 여부를 검사한다. 미등록 속성은 명시적인 `설정 필요` 상태로 보존한다. 맵의 접지 충돌·이동 가능성과 최종 시각 검수는 별도로 수행한다.

## 이미지 외형 설명

- `modeling.visual_description`: 원본 PNG를 확인한 형태·색상·구성·화면상 방향의 개별 묘사다. 용도와 배치 권고는 기존 `purpose`, `recommended_uses`, `composition`을 함께 읽는다.
- `modeling.visual_review`: 확인 날짜·방법·범위와 원본 SHA-256을 기록한다. `source_file.sha256`이 달라지면 재검수가 필요하다.
- 축소 검수 시트의 외형 확인은 정밀 알파 경계·게임 접지 검증을 대신하지 않는다. 화면상 왼쪽/오른쪽은 이미지 기준이며 실제 지면 점유 축은 `placement`를 따른다.
