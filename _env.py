# -*- coding: utf-8 -*-
"""표준 라이브러리만으로 같은 디렉토리의 .env 를 os.environ 에 로드한다.
docker 실행 시 -v 로 마운트된 /work/.env, 호스트 실행 시 스크립트 옆 .env 를 읽는다.
이미 설정된 환경변수는 덮어쓰지 않는다(setdefault)."""
import os


def load_env(path=None):
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
