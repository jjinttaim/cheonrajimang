# 실제 실종 사건과 독립 지형 자료

원자료 사건: Hashimoto et al. (2022), 실제 하이커 65건의 마지막 확인 위치·발견점. CC BY 4.0. 기존 `lost_hikers_2022/` 및 `prepared/incident_endpoints.csv`를 보존하며 모의 궤적·사건별 사후 적합값은 사용하지 않는다.

지형: [Copernicus GLO-90, AWS 공개 COG](https://registry.opendata.aws/copernicus-dem/), 제공자 Sinergise. 2026-09-19 접근. [공식 이용 조건](https://dataspace.copernicus.eu/sites/default/files/media/files/2025-06/copernicus_contributing_mission_data_access_v2_cop_dem_licenses.pdf)의 COP-DEM-GLO-90-F 절에 따른다. 내려받은 원문은 `assets/license/source`에 보존한다.

원 지형 자료 출처 표시:

© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.

지형 파생 특징·연구 결과 출처 표시:

produced using Copernicus WorldDEM™-90 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.

법률 또는 위임에 따라 Copernicus 프로그램을 담당하는 조직은 Copernicus WorldDEM™-90의 이용에 대해 책임을 부담하지 않는다. 본 활동은 제공자·ESA·EU의 공식 보증이나 승인을 받은 것이 아니다. 재배포 시 이용 조건과 출처·책임 관련 고지를 함께 유지한다.

이는 DSM(건물·식생 포함)이며 순수 지표 DTM이 아니다. 사건 날짜가 없어 사건 당시의 지형 일치를 확인하지 못했다. 원본 타일과 파생 파일은 이 프로젝트의 연구용 로컬 자료로 보관하며 개인의 신원·건강·연락처를 결합하지 않는다.

학습·채택 기준: [사전 검증 계획](../../../docs/AI-지형끝점-사전검증계획.md). 시간별 위치, 시설 방문, 낮밤 효과 또는 탐지확률을 학습하는 데이터가 아니다. 준비·학습 결과는 내용 해시별 하위 폴더에 보존하고 기존 앱 모델은 자동 변경하지 않는다.
