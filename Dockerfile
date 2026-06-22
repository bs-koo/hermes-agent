# Portable CloudWatch MCP server image (Hermes / MCP 클라이언트용)
# 공식 Docker Hub 이미지가 없어 직접 빌드한다. 이렇게 만들면
# 개발 서버(EC2 등)로 그대로 옮기거나 ECR에 push해 어디서든 동일 실행 가능.
#
# 빌드:
#   docker build -t cloudwatch-mcp:0.1.4 D:/SQ/hermes_agent
#
# 로컬 실행 (~/.aws 자격증명 마운트):
#   docker run --rm -i \
#     -v C:/Users/SQI/.aws:/root/.aws:ro \
#     -e AWS_PROFILE=default -e AWS_REGION=ap-northeast-2 \
#     -e FASTMCP_LOG_LEVEL=ERROR \
#     cloudwatch-mcp:0.1.4
#
# 개발 서버 실행 (EC2 인스턴스 IAM 역할 사용 시 — 자격증명 마운트 불필요):
#   docker run --rm -i -e AWS_REGION=ap-northeast-2 -e FASTMCP_LOG_LEVEL=ERROR cloudwatch-mcp:0.1.4

FROM python:3.12-slim

# MCP 서버 설치 (버전 고정 — 재현성/이식성)
RUN pip install --no-cache-dir "awslabs.cloudwatch-mcp-server==0.1.4"

# MCP는 stdio로 통신한다. docker run -i 로 실행.
ENTRYPOINT ["awslabs.cloudwatch-mcp-server"]
