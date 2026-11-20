// Ledgerly's CTO, for the demo recorder and its dry runs: the sign-in details come from ~/.whitehats-demo (never printed).
import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

export const BASE = process.env.WH_LIVE_URL || 'https://d1sminperzg6yf.cloudfront.net'
export const CTO = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.whitehats-demo', 'ledgerly-cto.json'), 'utf8'))

// The six-digit code an authenticator app shows now (RFC 6238, SHA-1, 30 seconds).
export function totp(secret, at = Date.now()) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  let bits = ''
  for (const ch of secret.toUpperCase()) if (alphabet.includes(ch)) bits += alphabet.indexOf(ch).toString(2).padStart(5, '0')
  const key = Buffer.from(Array.from({ length: Math.floor(bits.length / 8) }, (_, i) => parseInt(bits.slice(i * 8, i * 8 + 8), 2)))
  const counter = Buffer.alloc(8)
  counter.writeBigUInt64BE(BigInt(Math.floor(at / 30000)))
  const digest = crypto.createHmac('sha1', key).update(counter).digest()
  const o = digest[digest.length - 1] & 15
  return String((digest.readUInt32BE(o) & 0x7fffffff) % 1_000_000).padStart(6, '0')
}
