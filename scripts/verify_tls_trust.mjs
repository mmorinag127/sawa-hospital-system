import https from 'node:https';

const certificateErrors = new Set([
  'UNABLE_TO_GET_ISSUER_CERT_LOCALLY', 'UNABLE_TO_VERIFY_LEAF_SIGNATURE',
  'CERT_UNTRUSTED', 'CERT_REVOKED', 'SELF_SIGNED_CERT_IN_CHAIN',
]);

function request(url) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { timeout: 20000 }, res => {
      res.resume();
      resolve(res.statusCode);
    });
    req.on('timeout', () => req.destroy(new Error('TLS probe timed out')));
    req.on('error', reject);
  });
}

if (await request('https://pypi.org/') !== 200) throw new Error('Positive control failed');
for (const host of ['dv-g3b-emudhra', 'dv-g3b-aaa-emudhra']) {
  try {
    await request(`https://${host}.we-have-a-problem.com/`);
  } catch (error) {
    if (!certificateErrors.has(error.code)) throw error;
    console.log(JSON.stringify({ host, rejected: true, code: error.code }));
    continue;
  }
  throw new Error(`Compromised chain accepted: ${host}`);
}
