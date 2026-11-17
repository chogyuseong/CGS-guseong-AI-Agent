"""에이전트: Pydantic AI Agent, 도구 묶음, 실행 기록, 상한, 실행기.

읽는 순서(면접 설명용):
1. runner.py   - task 하나를 처음부터 끝까지 실행하는 흐름
2. agent.py    - Agent 정의(지시문, 모델 선택, Step 기록 capability)
3. tracking.py - 도구 호출마다 steps 행을 남기는 방법
4. toolsets.py - 위험 등급별 도구 묶음과 origin별 조합
5. budget.py, history.py, model_choice.py - 상한, 이전 기록, 모델 문자열 해석·호출 설정
"""
