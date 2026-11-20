/** A fake AWS account (moto with IAM enforced, backend/tests/fake_aws_server.py) the app is pointed at. Never real AWS. */

import { spawn } from 'node:child_process'
import path from 'node:path'
import readline from 'node:readline'
import { fileURLToPath } from 'node:url'

const BACKEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../backend')

/** `withFunction` adds one Lambda function to the account, for a journey that needs a function to draw to; every other test keeps the same eight resources. */
export async function startFakeAws({ withFunction = false } = {}) {
  const env = withFunction ? { ...process.env, FAKE_AWS_FUNCTION: '1' } : process.env
  const child = spawn('uv', ['run', 'python', 'tests/fake_aws_server.py'], { cwd: BACKEND, env, stdio: ['pipe', 'pipe', 'ignore'] })
  const lines = readline.createInterface({ input: child.stdout })
  const waiting = []
  lines.on('line', (line) => waiting.shift()?.(JSON.parse(line)))
  const next = () => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('The fake AWS account did not answer in time.')), 60_000)
    waiting.push((value) => { clearTimeout(timer); resolve(value) })
  })
  const info = await next()
  return {
    ...info,
    // What the app needs to reach the fake account instead of AWS. The keys are moto's own.
    env: {
      AWS_ENDPOINT_URL: info.endpoint, AWS_ACCESS_KEY_ID: info.access_key_id,
      AWS_SECRET_ACCESS_KEY: info.secret_access_key, AWS_DEFAULT_REGION: info.region,
    },
    /** Take one IAM action away from the read-only role, as a customer might. */
    revoke: async (action) => {
      child.stdin.write(`revoke ${action}\n`)
      return next()
    },
    stop: async () => {
      child.stdin.end()
      await new Promise((resolve) => child.once('exit', resolve))
    },
  }
}
