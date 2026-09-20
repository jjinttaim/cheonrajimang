# 탐지 연구 원문 — 참고용, 학습 입력 아님

2026-09-19 발행사에서 PDF 3개(총 74쪽)를 수집했다. 원본은 `objects/<SHA-256>.pdf`, 수집 영수증과 페이지·링크·첨부 목록은 `receipts/6d70dc3a056fd858f7caceb10412246efe36bc81a8b263c5661af73df49c8282.json`에 있다. 재수집은 새 영수증을 만들고 기존 파일을 덮어쓰지 않는다.

| 원문 | 확인한 자료 수준 | 이번 사용 |
|---|---|---|
| [Chiacchia & Houlahan, 2023](https://journalofsar.com/wp-content/uploads/2023/01/jsar_v6i1-chiaccchia.pdf), DOI 10.61618/NPEN4588 | 사람/구조견 실험의 누적 탐지 곡선과 적합된 W·SD·N. 표 1은 개별 발견/미발견 행이 아님 | 실험 설계 참고. 학습 제외 |
| [Chiacchia & Scelza, 2023](https://journalofsar.com/wp-content/uploads/2023/10/jsar-v6i2-1-chiacchia-truncated-exercise.pdf), DOI 10.61618/GNLN7584 | 단축 코스의 곡선·요약 적합값. 일부 적합이 불량한 결과도 포함 | 작은 실험의 한계 참고. 학습 제외 |
| [Chiacchia, Billings & Houlahan, 2025](https://journalofsar.com/wp-content/uploads/2025/08/v8i1_chiacchia_v3.pdf), DOI 10.61618/FLEB2002 | 시야 간격 측정, 탐지폭 적합, 그 값으로 계산하는 예상 POD | 예상 POD를 실제 관측 라벨로 변환하지 않음 |

PDF에 내장된 파일은 각각 0개다. 열거한 URI 주석에서 별도 행 단위 데이터 아카이브 링크를 찾지 못했다. **다른 곳에도 원자료가 없다는 뜻은 아니다.** 일부 그래프는 실제 관측을 누적한 값이므로 그 점들을 서로 독립인 사람/시행으로 취급하지 않는다. 논문에 포함된 `virtual placement`(물체를 실제 배치하지 않고 미탐지로 처리한 위치)도 직접 관측과 구분해야 한다.

이번 처리에서 생성한 학습 행은 **0개**다. 원문의 사실·출처를 검토한 것이며, 모든 관측을 받아 재학습했다는 뜻이 아니다. 앱의 탐지폭 8/15/22 m는 여전히 구현 가정이고, 이 문헌으로 국내 현장 보정이 끝났다고 표시하지 않는다.

## 보존·이용 범위

- 공개 열람 원문을 내부 연구 검토용으로 보존했다. 명시적 재배포 라이선스는 확인하지 못해 `NOT_VERIFIED_DO_NOT_REDISTRIBUTE`로 기록했다. 앱 인계 ZIP/공개 배포물에 포함하지 않는다.
- 모델·수색 기록·기본 파라미터는 수정하지 않는다. 연구 자료는 실행 서버의 공개 정적 디렉터리가 아니다.
- `pipeline/collect_detection_sources.py`는 정해진 발행사 URL만 요청한다. 임무/개인 위치를 전송하지 않으며, 문서의 링크를 자동으로 따라가거나 첨부 코드를 실행하지 않는다.
- 문서용 Python(pypdf 설치 환경)에서 `python -B -m pipeline.collect_detection_sources`로 재수집할 수 있다. 발행사 파일의 쪽수가 바뀌면 자동 채택하지 않고 오류로 중단한다. 원본 변경은 SHA-256이 다른 파일로 보존한다.

추가 접근 확인: [USCG 2004 보고서](https://www.dco.uscg.mil/Portals/9/CG-5R/nsarc/DetExpReport_2004_final_s.pdf)는 이번 직접 요청이 HTTP 403이었다. 받지 못한 원자료를 확보했다고 표시하지 않았다. [ISRID 공식 안내](https://www.dbs-sar.com/SAR_Research/ISRID.htm)의 내려받기 링크는 빈 수집 양식이며 전체 사건 관측 아카이브 접근권이 아니다. 이 오래된 안내의 사건 수를 현재 규모로 인용하지 않는다.

남은 실제 학습에 필요한 자료와 처리 원칙은 [자료 확보 상태](../../../docs/AI-남은학습자료-확보상태.md)를 참조한다.
