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
| Copernicus DEM GLO-30 (표고) | Copernicus 이용 조건 — 출처 표시 유지 시 상업 이용 가능 | `data/hallim/dem.npy`, 경사·이동비용 | 가능. 출처 문구 유지 |
| ESA WorldCover 2021 (토지피복) | CC BY 4.0 | `data/hallim/landcover.npy` | 가능. 출처 표시 |
| OpenStreetMap 도로·시설 추출물 | ODbL 1.0 | `data/hallim/roads.geojson`, 시설 630곳 | 가능. 파생 DB 공개 의무(ODbL share-alike)를 검토하고 출처 표시 |
| OpenFreeMap 벡터 타일 (표시용 지도) | 공개 무료 서비스, OSM ODbL 데이터 | 화면 배경 지도 | 대량 트래픽·SLA가 필요하면 자체 타일 서버 또는 유료 타일 공급자로 교체 |
| 실제 실종 하이커 65건 (Hashimoto et al. 2022, Sci. Rep. 부록) | CC BY 4.0 | 거리 시나리오 A 학습 모델(기본), 발견점 참고 모드 | 가능. 논문·부록 출처 표시. ISRID 전체 DB 권한은 아님 |
| 수색대 GPS 61트랙 (Hashimoto et al. 2026, Zenodo 18637221) | CC BY 4.0 | 수색대 속도 Random Forest | 가능. 출처 표시 |
| 키프로스 수색 훈련 GPS (KIOS, Zenodo 6592419) | CC BY 4.0 | 품질검사만, 학습 미사용 | 가능 |
| Copernicus GLO-90 타일 31개 | Copernicus 이용 조건 | 연구용 지형 계수 모델(미채택) | 앱 미포함 |
| LiDAR 보행 궤적 (offtrail 2025) | CC BY 4.0 | 검사만, 학습 미사용 | 앱 미포함 |

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
