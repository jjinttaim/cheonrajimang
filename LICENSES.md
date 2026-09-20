# 천라지망 — 자료·모델·구성요소 이용 조건 매트릭스

작성 2026-09-20. 프로젝트가 실제로 사용하거나 보관 중인 모든 외부 자료의 이용 조건과, 각 조건이 **공모전 제출·훈련용 배포·상용 확산**에 미치는 영향을 한 표로 정리한다. 상세 출처와 해시는 각 폴더의 `NOTICE.md`·`manifest.json`에 있다.

두 가지 배포 프로필로 운영한다.

| 프로필 | 환경 변수 | 포함 | 용도 |
|---|---|---|---|
| `research` (기본) | `SEARCHPROOF_DEPLOY_PROFILE=research` | 모든 구성요소 | 공모전 시연, 교육·비상업 훈련 |
| `commercial-ready` | `SEARCHPROOF_DEPLOY_PROFILE=commercial-ready` | 비상업 조건 자료를 쓰는 기능을 서버에서 비활성화(YOSAR 모델 선택지 제거, EOX 위성 배경 숨김, GeoLife 보행 실험실 503) | 등록 단체 실사용·지자체 실증처럼 상업 판단이 애매한 확산 단계 |

프로필은 실행 시 `/api/base`의 `deploy_profile`·`noncommercial_enabled`로 확인할 수 있고, 새로 만드는 임무의 영수증에는 사용한 모델 묶음과 자료 출처가 그대로 남는다.

## 1. 기본 지도에 쓰이는 자료 (모든 프로필)

| 자료 | 이용 조건 | 프로젝트 내 사용 | 상용 확산 시 |
|---|---|---|---|
| Copernicus DEM GLO-30 (표고) | Copernicus DEM 이용 조건(COP-DEM-GLO-30-F) — 아래 출처·면책 문구를 유지하면 재배포·상업 이용 가능 | `data/jeju/dem.npz`, `data/hallim/dem.npy`, 경사·이동비용 | 가능. 출처·면책 문구 유지 |
| ESA WorldCover 2021 v200 (토지피복) | CC BY 4.0 | `data/jeju/landcover.npz`, `data/hallim/landcover.npy` | 가능. 아래 출처 문구·DOI 표시 |
| OpenStreetMap 도로·시설 추출물 | ODbL 1.0 | `data/jeju/roads.geojson.gz`·`road_distance.npz`·`road_mask.npz`·`facilities/*/facilities.geojson`(섬 전체 POI 12,906곳), `data/hallim/roads.geojson`·`road_distance.npy`·`facilities/*/facilities.geojson`·`potential.npy`(한림 창 630곳) | 가능. 이 파생 DB는 아래 선언대로 ODbL 1.0으로 공개하고 출처 표시 |
| OpenFreeMap 벡터 타일 (표시용 지도) | 공개 무료 서비스, OSM ODbL 데이터 | 화면 배경 지도 | 대량 트래픽·SLA가 필요하면 자체 타일 서버 또는 유료 타일 공급자로 교체 |
| 실제 실종 하이커 65건 (Hashimoto et al. 2022, Sci. Rep. 부록) | CC BY 4.0 | 거리 시나리오 A 학습 모델(기본), 발견점 참고 모드 | 가능. 논문·부록 출처 표시. ISRID 전체 DB 권한은 아님 |
| 수색대 GPS 61트랙 (Hashimoto et al. 2026, Zenodo 18637221) | CC BY 4.0 | 수색대 속도 Random Forest | 가능. 출처 표시 |
| 키프로스 수색 훈련 GPS (KIOS, Zenodo 6592419) | CC BY 4.0 | 품질검사만, 학습 미사용 | 가능 |
| Copernicus GLO-90 타일 31개 | Copernicus DEM 이용 조건 | 연구용 지형 계수 모델(미채택). 원본 타일·제공자 문서(`assets/`)는 로컬 전용, 파생 특성 `data/research/endpoint_terrain_2026/<해시>/features.npz`와 실행 기록(`runs/*.json`, `latest_run.json`, `prepared.json`)만 저장소 포함 | 앱 미포함 |
| LiDAR 보행 궤적 (offtrail 2025, Zenodo 17081136) | CC BY 4.0 | 검사만, 학습 미사용. 원본 ZIP `data/research/offtrail_lidar_2025/trajectories.zip`은 저장소 포함 | 앱 미포함 |

### 출처 표시 문구 (저장소 문서 기준)

- **Copernicus DEM GLO-30**: "Produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved." 면책: "The organisations in charge of the Copernicus programme by law or by delegation do not incur any liability for any use of the Copernicus WorldDEM-30." 공식 보증을 받은 것처럼 표현하지 않으며, 재배포 받는 쪽에도 같은 의무가 전달된다(6조 d·e). 배포처: [AWS Open Data — Copernicus DEM](https://registry.opendata.aws/copernicus-dem/).
- **Copernicus DEM GLO-90 파생물**(`data/research/endpoint_terrain_2026/<해시>/features.npz`): 문구는 `data/research/endpoint_terrain_2026/NOTICE.md`의 WorldDEM™-90 고지를 그대로 사용한다.
- **ESA WorldCover 2021 v200**: "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium." 인용: Zanaga, D. et al. (2022) *ESA WorldCover 10 m 2021 v200*, [doi:10.5281/zenodo.7254221](https://doi.org/10.5281/zenodo.7254221). 라이선스 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- **OpenStreetMap 파생 DB (ODbL 1.0 선언)**: 위 표의 OSM 파생 파일은 "© OpenStreetMap contributors" 자료를 가공한 파생 데이터베이스이며, 이 저장소에서 [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/)으로 공개한다. 변경 내용(추출 범위·태그·격자화 방법)은 `pipeline/prepare_jeju.py`, `pipeline/prepare.py`, 각 `facilities/<해시>/manifest.json`의 Overpass 질의에 기록돼 있다. 화면 지도(OpenFreeMap 벡터 타일)는 조정자 화면과 참여자 화면 모두에 "OpenFreeMap, © OpenMapTiles, © OpenStreetMap contributors"를 표시한다.
- `data/jeju/meta.json`·`data/hallim/meta.json`의 `attribution` 값은 위 문구의 요약본이며, 화면의 자료 출처 창과 인계 보고서에는 이 요약본이 실린다. 전문과 ODbL URI를 화면·보고서·meta.json에도 싣는 것은 남은 과제다.

## 2. 비상업 조건 자료 (`research` 프로필에서만 활성)

| 자료 | 이용 조건 | 프로젝트 내 사용 | 대체 경로 |
|---|---|---|---|
| YOSAR Missing Person Data v1 (Doherty & Doke, NSF #1031914) | CC BY-NC-SA 4.0 | AI 계획의 "발견점 참고" 모드에서 명시 선택하는 별도 132건 구간 모델 | `commercial-ready`에서 자동 비활성. 상용에서는 65건 CC BY 모델만 사용하거나 저자와 별도 계약 |
| EOX Sentinel-2 cloudless 2025 (위성 배경) | CC BY-NC-SA 4.0 (교육·비상업) | 표시용 위성 배경 전환 | `commercial-ready`에서 버튼 숨김. 상용에서는 국토지리정보원 정사영상·브이월드 API·상업 위성 타일로 교체 |
| GeoLife 1.3 (Microsoft Research) | MSR-LA 비상업 전용, 재배포 금지 | 일반 보행 실험실(미채택 연구 모델) | `commercial-ready`에서 503. 원본·정제 자료·모델은 로컬 보관, 어떤 패키지에도 포함하지 않음 |
| 탐지 연구 PDF 3편 (Journal of SAR) | 열람 공개, 재배포 조건 미확인 (`NOT_VERIFIED_DO_NOT_REDISTRIBUTE`) | 내부 참고. 학습 행 0개 | 인계 ZIP·공개 배포물에 포함하지 않음 |

## 3. 소프트웨어 의존성

| 구성 | 라이선스 | 비고 |
|---|---|---|
| FastAPI, Starlette, pydantic, uvicorn | MIT | 서버 |
| NumPy, SciPy, rasterio | BSD-3 | 계산·지형 전처리 |
| pyproj | MIT | 좌표 변환 |
| Pillow | MIT-CMU (HPND) | 지도 PNG |
| defusedxml | PSF | GPX 파싱 |
| scikit-learn | BSD-3 | 학습 파이프라인만. 실행 서버는 JSON 트리를 직접 계산해 의존하지 않음 |
| React, Vite, lucide-react | MIT / MIT / ISC | 화면 |
| MapLibre GL JS | BSD-3 | 지도 |
| IBM Plex Sans KR, Manrope (Google Fonts) | SIL OFL 1.1 | 글꼴. 오프라인 배포 시 폰트 파일 동봉 가능 |

## 4. 프로젝트 산출물

- 코드·문서·학습 스크립트: 팀 저작물. 공모전 규정에 따라 주최 측 이용 범위를 확인해 표기한다.
- 학습 모델 묶음 `models/<해시>/`: 65건·61트랙 모델은 CC BY 자료 파생물로 재배포 가능(출처 표시). YOSAR 파생 파라미터(`endpoint.json`의 `yosar_interval_lognorm`)는 CC BY-NC-SA 4.0을 따른다 — 상용 배포 묶음에서는 이 항목을 제거한 새 묶음을 학습해야 한다.
- 인계 패키지 ZIP: 위성 영상·연구 PDF·GeoLife 관련 파일을 포함하지 않는다. 포함되는 것은 지형 출처 메타데이터, 계산 배열, 영수증, 장부, 참여자 요약(코드 제외), 사용 모델의 파라미터·출처·라이선스다.

## 5. 기획서 확산 계획과의 정합

기획서는 훈련용 배포 → 등록 단체 실사용 → 지자체 협력 실증 순의 확산을 적는다. 등록 단체 실사용부터는 상업 여부가 모호해질 수 있으므로 그 단계에서 `commercial-ready` 프로필로 전환하고, 위성 배경과 YOSAR 모델을 위 대체 경로로 바꾼다. 이 전환은 기본 지도·수색 기록·인계 기능에 영향을 주지 않는다(모두 CC BY·ODbL·Copernicus 자료).

## 6. 깃허브 저장소 포함 범위 (2026-09-20)

저장소 [github.com/jjinttaim/cheonrajimang](https://github.com/jjinttaim/cheonrajimang)에는 아래 표의 "저장소 포함" 열에 적은 항목만 들어 있다. 로컬 전용 항목은 `.gitignore`로 제외했고, 이를 읽는 백엔드 테스트는 파일이 없으면 자동으로 건너뛴다(`pytest` 결과의 `skipped`).

| 구분 | 저장소 포함 | 로컬 전용(미포함) |
|---|---|---|
| 지형·시설 격자 | `data/jeju/*`, `data/hallim/*` (Copernicus·WorldCover·OSM 파생, §1 문구 유지) | — |
| 학습 모델 | `models/current.json`(현재 묶음 포인터), `models/<해시>/` 3묶음 (65건·61트랙 모델은 CC BY 파생; 두 묶음 `e070…`·`fa9c…`의 `endpoint.json`에 든 YOSAR 파라미터 `yosar_interval_lognorm`은 CC BY-NC-SA 4.0) | GeoLife 보행 모델 `data/models/walking_reference/` (MSR-LA) |
| 연구 원본 | `data/research/lost_hikers_2022/` CSV 3개·부록 PDF 1개 (CC BY 4.0), `sar_searchers_2026/` (CC BY 4.0), `cyprus_exercise_2022/` (CC BY 4.0), `offtrail_lidar_2025/trajectories.zip` (CC BY 4.0), `yosar_endpoints_2000_2010/` JSON (CC BY-NC-SA 4.0) | GeoLife 1.3 원본·정제 자료 `data/research/geolife_2012/`·`data/ml/geolife/` (MSR-LA, 재배포 금지), 탐지 연구 PDF `data/research/detection_sources_2026/objects/` (재배포 조건 미확인), GLO-90 타일·제공자 문서 `data/research/endpoint_terrain_2026/assets/` (약 149 MB, 용량 때문에 제외 — 이용 조건상 재배포는 가능) |
| 연구 파생물·기록 | `data/research/prepared/*.csv`, `endpoint_terrain_2026/<해시>/features.npz`·`manifest.json`·`runs/*.json`·`latest_run.json`·`prepared.json`, `detection_sources_2026/receipts/*.json`(페이지 목록·해시만), 각 폴더의 `NOTICE.md`·`README.md`·`manifest.json`·`record.json`·`quality_report.json` (GeoLife 폴더 `geolife_2012/`는 NOTICE 포함 통째로 제외) | — |
| 실행·검사 산출물 | — | `data/runtime/`(임무 DB), `artifacts/`(스크린샷·샘플 인계물), `.venv*/`, `frontend/node_modules/`, `frontend/dist/`, `data/raw/`, `_handoff/`·`_claude_backup_2026-09-20/`(옛 원본·인계 사본) |

로컬 전용 자료가 없어도 서버와 기본 기능은 그대로 동작한다. 해당 기능은 503 응답 또는 구성요소 표의 `UNKNOWN` 상태로 표시된다.
