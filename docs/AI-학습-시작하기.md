# 천라지망 — 무엇을 학습하고 어떻게 검증할까

목표 구체화: **실시간 수색 범위 추천**이 목표라면 [천라지망 AI 학습 설계](천라지망-AI-학습설계.md)를 먼저 본다. 아래의 수색대 속도 학습은 입문·보조 실험이며, 실종자 위치분포 학습이나 수색 구역 선택 모델을 대신하지 않는다.

작성: 2026-09-19 학습 전 입문 안내. 이후 발견 거리·수색대 속도 모델을 실제 학습하고 앱에 연결했다. 실행 결과와 현재 범위는 [학습 결과와 사용법](AI-학습결과와-사용법.md)을 먼저 확인한다. 아래 예시 설정은 최초 안내이며 실제 고정 학습 설정은 `AI-구현-검증계획.md` 및 `pipeline/train_models.py`를 따른다. 실제 수색 성능 검증은 아직 완료하지 않았다.

## 1. 한 개의 거대한 AI보다, 정답이 있는 작은 모델부터

| 학습 대상 | 입력 → 정답 | 확보 자료 | 지금 가능한 범위 |
|---|---|---|---|
| 수색대 이동속도 | 경사·수색 방식 → 수색자 GPS 구간속도 | 61트랙, 45,202점 | 소규모 회귀모델 비교. 실종자 이동속도와 구분 |
| 실종 사건 발견점 분포 | 마지막 위치·주변 지형 → 발견 위치 | 하이커 실제 사건 65건 | 사건별 평가를 둔 거리 분포 기준 모델. 연속 경로/시각별 위치 학습은 불가 |
| 낮·밤·시설의 행동 효과 | 시각·조도·시설 접근성 → 이동/정지/방문 | 필요한 정답 미확보 | 현재는 가정 비교. 관측 자료를 추가한 후 학습 |
| 탐지확률(POD) | 수색 간격·식생·밝기 → 표식을 발견했는가 | 필요한 정답 미확보 | 별도 블라인드 표식 실험 필요 |

GPS 45,202점은 독립적인 실종 사건 45,202건이 아니다. 수색대 트랙에 마지막 확인 위치·발견 위치의 정답을 억지로 붙이거나, 시뮬레이션을 실제 사례로 섞지 않는다. 상세 출처·파일은 [공개 자료 안내](../data/research/README.md)에 있다.

## 2. 첫 학습 추천: 수색대 속도 모델

이 모델은 미래의 **수색 소요시간·인원 배치 보조**용이다. 현재 실종자 확률지도의 이동속도나 POD를 바로 대체하지 않는다. 실종자와 훈련된 수색대는 행동이 다르다.

1. 파일: `data/research/prepared/searcher_segments.csv`.
2. 정답 `y`: `speed_mps`. 정지에 가까운 구간도 목적에 따라 중요한 기록이므로 좋은 성능을 만들려고 일괄 제거하지 않는다.
3. 입력 `X`: 우선 `grade`(경사), `search_type`(수색 방식)만. `distance_m`, `delta_seconds`는 속도를 계산하는 값이므로 넣지 않는다. `track_id`, `team_id`, 원본 행 번호도 예측 입력에서 제외한다.
4. 품질: 기존 공백/이상속도 표시를 확인하고, 제외 규칙을 학습 전에 고정한다. `CANDIDATE`는 품질이 확정된 정답이라는 뜻이 아니다. 정지점의 경사 결측값은 미래에 알 수 없는 정지 여부를 입력으로 주입하지 않도록 취급해야 한다. 첫 실험에서는 **경사가 관측된 구간만** 사용하고, 정지/경사 결측 구간의 성능은 평가하지 못한다고 명시한다. 전 행을 예측하려면 독립 DEM 경사를 보강한다.
5. 분할: 같은 팀의 구간들이 훈련과 평가에 함께 들어가지 않게 `GroupKFold`를 사용한다. 익명 사건 ID가 없어 서로 다른 팀이 같은 사건·지형에 참여했는지는 완전히 차단할 수 없다. 팀별 분할은 사건 독립 검증의 대체 증명이 아니다.
6. 비교: 훈련 자료의 수색 방식별 중앙속도 예측과, 경사+방식을 보는 작은 Random Forest를 비교한다. 복잡한 모델이 단순 기준보다 나쁘면 단순 기준을 유지한다.
7. 결과: 보지 않은 팀의 MAE(평균 절대 오차, m/s)를 비교한다. 트랙별 MAE를 먼저 계산하고 평균해 긴 트랙 하나가 평가를 지배하지 않게 한다. 각 팀/방식 결과와 제외 수를 함께 보고한다.

처음에는 **Python + pandas + scikit-learn, CPU**로 충분한 규모의 실험을 설계할 수 있다. GPU 대여나 ChatGPT 미세조정부터 시작할 이유는 없다. 다음은 프로젝트 루트에서 별도 학습 환경을 만들 때의 예시이며, 현재 실행 환경에 자동 설치하지 않았다.

```sh
python3 -m venv .venv-ml
.venv-ml/bin/python -m pip install pandas scikit-learn
```

핵심 학습 예시(아직 실행하지 않은 시작 코드). 아래 점수로 설정을 반복 튜닝하면 최종 검증이 아니라 개발 검증이 된다. 본선용 확정 결과에는 별도 보류 집단 또는 중첩 그룹 검증을 둔다.

```python
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder
from sklearn.pipeline import make_pipeline
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import GroupKFold

raw = pd.read_csv("data/research/prepared/searcher_segments.csv")
# 경사 결측을 정지 여부의 대리 정답으로 쓰지 않는 제한된 첫 실험
d = raw.loc[(raw.quality_flag == "CANDIDATE") & raw.grade.notna()].reset_index(drop=True)
print("제외 행:", len(raw)-len(d), "트랙:", d.track_id.nunique())
X, y = d[["grade", "search_type"]], d.speed_mps
groups = d.team_id.astype(str)
cv = GroupKFold(n_splits=min(5, groups.nunique()))
oof = pd.DataFrame(index=d.index, columns=["model", "baseline"], dtype=float)

for train, test in cv.split(X, y, groups):
    assert set(groups.iloc[train]).isdisjoint(groups.iloc[test])
    transform = ColumnTransformer([
        ("slope", "passthrough", ["grade"]),
        ("type", OneHotEncoder(handle_unknown="ignore", sparse_output=False), ["search_type"]),
    ])
    model = make_pipeline(transform, RandomForestRegressor(
        n_estimators=100, max_depth=6, min_samples_leaf=30,
        random_state=42, n_jobs=2))
    model.fit(X.iloc[train], y.iloc[train])
    oof.loc[test, "model"] = model.predict(X.iloc[test])
    medians = d.iloc[train].groupby("search_type").speed_mps.median()
    oof.loc[test, "baseline"] = X.iloc[test].search_type.map(medians).fillna(y.iloc[train].median())

for name in ("baseline", "model"):
    errors = (oof[name] - y).abs()
    print(name, "트랙별 MAE 평균(m/s):", errors.groupby(d.track_id).mean().mean())
```

주의: 공개 파일의 경사는 GPS 고도차/이동거리로 만든 회고적 관측이다. 실제 서비스에서 쓰는 DEM 기반 경사와 오차 특성이 다르다. 이 예시 결과를 곧바로 지도 셀의 사전 속도 모델 성능이라고 주장하지 않는다. 계수·특징·제외 기준을 바꾸면 새 실험으로 기록하고 불리한 결과도 보존한다.

## 3. 실종자 위치 AI는 별도로 시작

파일은 `data/research/prepared/incident_endpoints.csv`이다. 목표는 먼저 **이 자료와 유사한 하이커 사건의 발견점 분포**를 비교하는 것이다. 발견점은 실제 이동 중 모든 시각의 위치와 같지 않으며, 구조·신고·표본 선택의 영향을 받는다.

- 먼저 거리 기준 분포를 적합한다. 훈련 사건의 시작→발견 거리를 사용하고, 남겨 둔 사건의 발견 위치에서 얼마나 잘 맞는지 평가한다.
- 65건을 사건 단위로 나눈다. 한 사건에서 생성한 수천 개 입자를 서로 다른 평가 묶음에 넣지 않는다.
- 고정된 지역 범위·격자·지도 밖 질량을 사용해 발견 셀의 음의 로그 점수, 상위 10/20/30% 면적 적중률을 비교한다. 지형 모델 개선은 해당 사건 지역의 DEM·도로를 별도로 확보한 뒤 한다. 제주 지형을 미국 사건 좌표에 적용하지 않는다.
- 경과시간·실제 경로가 없으므로 `거리/시간` 속도 라벨, 밤 방문 라벨, 시설 방문 라벨을 만들지 않는다.
- `MOESM1`은 모의 결과, `MOESM2`는 발견점을 보고 사건별로 적합한 행동 파라미터다. 같은 사건의 예측 입력으로 쓰면 정답 누출이다.
- 미국 하이커의 작은 선택 표본이다. 한국의 아동·치매·차량 실종에 일반화했다고 발표하지 않는다.

## 4. 낮·밤과 시설 효과를 실제로 학습하려면

현재 61트랙은 절대 날짜·시각과 사건 메타데이터가 익명화 과정에서 제거됐다. 상대시간 0초가 밤인지 낮인지 알 수 없다. 위치만으로 시각을 추측하거나 수집일을 관측일로 대신 넣지 않는다. [저자 자료](https://zenodo.org/records/18637221)

후속으로 필요한 최소 관측은 `동의된 참가자 ID, 훈련 회차 ID, 시간대 포함 시각, 위치·GPS 오차, 독립 DEM 경사, 밝기/조명, 이동·정지, 시설 실제 방문`이다. 개인·회차 단위로 평가를 분리하고 건강/사건 정보는 필요한 경우에만 별도 동의와 보호 절차를 둔다.

낮/해 질녘 비교 훈련은 허가받은 안전한 코스에서 성인 책임자 감독 아래 설계한다. 어두운 위험 지형에 사람을 보내거나 실제로 길을 잃게 하지 않는다. 정상 참가자의 보행 실험이 실제 실종자의 행동을 검증한 것은 아니다.

POD는 별도 실험이다. 표식이 실제로 있었는지와 찾아냈는지 정답을 기록하고, 보정 코스와 평가 코스를 나눈다. ‘밤이면 탐지확률 절반’ 같은 값은 관측 없이 학습 결과로 저장하지 않는다.

## 5. 앱에 붙이는 순서와 통과 조건

`원본+해시 고정 → 사용 열/제외/분할 규칙 고정 → 기준 모델 → 그룹 평가 → 실패 포함 결과 → 모델 파일+설정 기록 → 앱의 별도 비교 모드`

처음부터 기존 모형을 자동 교체하지 않는다. 모델 종류·훈련 자료 범위·학습 날짜·코드 및 파일 해시·입력 특성 목록·평가 결과를 model card로 남긴다. 계수와 변환 과정은 훈련 데이터에서만 학습한다. 단위·유효 범위·확률 합을 검사하고 입력이 학습 범위를 벗어나면 기존 가정 모델로 돌아간다.

**현재 첫 결정:** 수색대 속도 모델을 위 과정으로 먼저 시험하고, 실종자 모델은 시간 정답이 없는 발견점 기준 모델로 병행한다. 야간·시설·POD는 데이터가 추가되기 전까지 가정 표시를 유지한다.

## 참고

- [PLOS ONE — 수색자 행동 연구](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0339541): 수색대 속도·경사 자료의 범위.
- [scikit-learn 그룹별 검증](https://scikit-learn.org/stable/modules/cross_validation.html#cross-validation-iterators-for-grouped-data): 동일 집단의 훈련/평가 혼입 방지.
- [scikit-learn 누출 방지](https://scikit-learn.org/stable/common_pitfalls.html): 평가 자료를 보며 변환·튜닝하지 않는 원칙.
- [RandomForestRegressor](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestRegressor.html): 첫 비교 모델 구현 문서.
