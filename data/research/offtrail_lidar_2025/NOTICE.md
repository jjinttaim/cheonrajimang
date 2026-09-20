# 별도 보행 실험 원자료 — 아직 학습에 사용하지 않음

저자: Sierra Lynn Cutler, Michael J. Campbell, Philip E. Dennison (2025).
[Mobile Lidar Scanner Trajectories](https://doi.org/10.5281/zenodo.17081136).
원자료: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
연결 논문: [Machine learning estimation of off-trail pedestrian travel rates using LiDAR-derived slope, vegetation, and surface roughness](https://doi.org/10.1080/15481603.2026.2626632).

저자가 공개한 trajectories.zip과 기록 메타데이터를 보존한다. 배포자의 MD5와 다운로드 SHA-256을 검사했고 압축 파일을 추출하거나 실행하지 않았다. 이름을 바꾼 원자료나 모의 이동 데이터를 만들지 않았다. 저자의 제품 보증을 뜻하지 않는다.

이 자료는 통제된 야외 보행 실험이며 실제 실종자 경로가 아니다. LAS 좌표계·시간 필드·참가자/코스 묶음·독립 지형 특성을 확인한 뒤 수색 이동시간 보조 연구 또는 외부 점검에 사용할 수 있을지 판단한다. 논문의 예측 입력인 식생·지표 거칠기 자료가 이 궤적 ZIP에 모두 포함됐다고 가정하지 않는다. 논문의 예측 성능은 우리 모델의 성능이 아니다.

현재 학습 모델, 시간 이동 가설, 탐지확률에는 이 자료를 넣지 않았다. `manifest.json`에 파일별 해시·실제 압축 파일 목록과 현재 사용 상태를 기록한다.

후속 전수 검사: 165개 LAS, 39,962점, 39,797인접 구간을 읽었다. 모두 EPSG:26912이지만 상대시간 설명(밀리초)과 시간 필드의 수치 관계가 불일치하며, GPS 시간 필드는 128 단위로 뭉쳐 있다. 단위를 임의 수정하지 않고 학습에서 격리했다. `quality_report.json`과 [추가 데이터 검사](../../../docs/AI-추가데이터-검사.md)에 진단 가정·전체 파일 통계·재현 방법을 기록한다. 수집 당시 manifest와 ZIP은 보존한다.
