/**
 * CloudWatch Synthetics 캐너리 — dataviz-prod 가동률·응답시간 능동 측정
 * 런타임: syn-nodejs-puppeteer-7.0 이상 (executeHttpStep 사용)
 * 앱(서버) 무수정. CloudFront→EB 전체 경로의 사용자 체감 응답시간/가용성을 측정한다.
 *
 * 각 step(엔드포인트)마다 Duration/SuccessPercent 메트릭이 생성됨:
 *   네임스페이스 CloudWatchSynthetics, 차원 CanaryName + StepName
 *
 * 인증(Google OAuth) 필요한 /api/* 는 공개 접근 불가 → 공개 엔드포인트만 측정.
 *   - /actuator/health : 앱+인프라 헬스(200, 무인증)
 *   - /                : SPA 홈페이지 로딩(200, 무인증)
 */
const synthetics = require('Synthetics');
const log = require('SyntheticsLogger');

const HOST = 'app.gx-viz.com';

// 측정 대상(무인증 공개 엔드포인트). 인증 필요한 API는 2단계(로그인 캐너리)에서.
const STEPS = [
    { name: 'health',   path: '/actuator/health' },
    { name: 'homepage', path: '/' },
];

const apiCanary = async function () {
    const cfg = synthetics.getConfiguration();
    cfg.setConfig({
        includeRequestHeaders: false,
        includeResponseHeaders: false,
        includeRequestBody: false,
        includeResponseBody: false,
        continueOnHttpStepFailure: true, // 한 step 실패해도 나머지 측정 계속
    });

    for (const s of STEPS) {
        const requestOptions = {
            hostname: HOST,
            method: 'GET',
            path: s.path,
            port: 443,
            protocol: 'https:',
            headers: { 'User-Agent': synthetics.getCanaryUserAgentString() },
        };
        await synthetics.executeHttpStep(s.name, requestOptions, async function (res) {
            return new Promise((resolve, reject) => {
                if (res.statusCode < 200 || res.statusCode > 399) {
                    reject(new Error(`${s.name} 실패: HTTP ${res.statusCode}`));
                    return;
                }
                let body = '';
                res.on('data', (d) => { body += d; });
                res.on('end', () => { log.info(`${s.name} OK ${res.statusCode}`); resolve(); });
            });
        });
    }
};

exports.handler = async () => {
    return await apiCanary();
};
