# -*- coding: utf-8 -*-
"""서비스별 상태 데이터 가용성 확인 — EB / CloudFront / EC2 / RDS."""
import datetime, boto3

now = datetime.datetime.now(datetime.timezone.utc)
t0 = now - datetime.timedelta(days=1)


def stat(cw, ns, metric, dims, st="Average", period=86400):
    try:
        r = cw.get_metric_statistics(Namespace=ns, MetricName=metric, Dimensions=dims,
                                     StartTime=t0, EndTime=now, Period=period, Statistics=[st])
        dp = sorted(r["Datapoints"], key=lambda x: x["Timestamp"])
        return dp[-1][st] if dp else None
    except Exception as e:
        return f"ERR:{str(e)[:50]}"


cw = boto3.client("cloudwatch", region_name="ap-northeast-2")
cwe = boto3.client("cloudwatch", region_name="us-east-1")

print("=== Elastic Beanstalk ===")
print("  EnvironmentHealth:", stat(cw, "AWS/ElasticBeanstalk", "EnvironmentHealth",
                                   [{"Name": "EnvironmentName", "Value": "dataviz-prod"}]))

print("=== EC2 (i-0e3e7120bc0b07a2d) ===")
print("  CPUUtilization avg:", stat(cw, "AWS/EC2", "CPUUtilization",
                                    [{"Name": "InstanceId", "Value": "i-0e3e7120bc0b07a2d"}]))
print("  StatusCheckFailed:", stat(cw, "AWS/EC2", "StatusCheckFailed",
                                   [{"Name": "InstanceId", "Value": "i-0e3e7120bc0b07a2d"}], "Maximum"))

print("=== CloudFront (us-east-1) ===")
# DistributionId 탐색
ms = cwe.list_metrics(Namespace="AWS/CloudFront", MetricName="Requests")["Metrics"]
dist = None
for m in ms:
    for d in m["Dimensions"]:
        if d["Name"] == "DistributionId":
            dist = d["Value"]
if dist:
    cfdim = [{"Name": "DistributionId", "Value": dist}, {"Name": "Region", "Value": "Global"}]
    print("  DistributionId:", dist)
    print("  Requests(24h sum):", stat(cwe, "AWS/CloudFront", "Requests", cfdim, "Sum"))
    print("  4xxErrorRate avg:", stat(cwe, "AWS/CloudFront", "4xxErrorRate", cfdim))
    print("  5xxErrorRate avg:", stat(cwe, "AWS/CloudFront", "5xxErrorRate", cfdim))
else:
    print("  (CloudFront 배포 메트릭 없음)")
