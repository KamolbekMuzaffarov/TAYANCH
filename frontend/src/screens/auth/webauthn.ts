// WebAuthn (passkey) helpers: the server speaks base64url JSON, the browser wants ArrayBuffers.
import type { Json } from '../../lib/api'

export const passkeySupported = (): boolean =>
  typeof window !== 'undefined' && typeof window.PublicKeyCredential !== 'undefined' && !!navigator.credentials

export function b64uToBuf(s: string): ArrayBuffer {
  const b64 = s.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (s.length % 4)) % 4)
  const bin = atob(b64)
  const out = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
  return out.buffer
}
export function bufToB64u(buf: ArrayBuffer | null | undefined): string | null {
  if (!buf) return null
  const bytes = new Uint8Array(buf)
  let bin = ''
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

const isObj = (v: unknown): v is Json => typeof v === 'object' && v !== null && !Array.isArray(v)
const creds = (v: unknown): PublicKeyCredentialDescriptor[] | undefined =>
  Array.isArray(v)
    ? v.filter(isObj).map((c) => ({
      type: 'public-key' as const, id: b64uToBuf(String(c.id)),
      ...(Array.isArray(c.transports) ? { transports: c.transports as AuthenticatorTransport[] } : {}),
    }))
    : undefined

/** Thrown when the person closes the system sheet — the UI stays quiet about it. */
export class PasskeyCancelled extends Error { constructor() { super('cancelled'); this.name = 'PasskeyCancelled' } }
function translate(e: unknown): never {
  const name = e instanceof Error ? e.name : ''
  if (name === 'NotAllowedError' || name === 'AbortError') throw new PasskeyCancelled()
  if (name === 'InvalidStateError') throw new Error('Bu qurilma allaqachon qoʻshilgan.')
  if (name === 'SecurityError') throw new Error('Passkey bu manzilda ishlamaydi. Sahifani localhost yoki HTTPS orqali oching.')
  throw new Error('Passkey ishlamadi. Boshqa usulni tanlang.')
}

/** Sign in (or pass the second step) with a passkey. Returns the body for `/account/passkey/login/verify` (without `state`). */
export async function getAssertion(publicKey: Json): Promise<Json> {
  const opts: PublicKeyCredentialRequestOptions = {
    challenge: b64uToBuf(String(publicKey.challenge)),
    rpId: typeof publicKey.rpId === 'string' ? publicKey.rpId : undefined,
    timeout: typeof publicKey.timeout === 'number' ? publicKey.timeout : 60000,
    userVerification: (publicKey.userVerification as UserVerificationRequirement) ?? 'required',
    allowCredentials: creds(publicKey.allowCredentials),
  }
  let cred: PublicKeyCredential | null
  try { cred = (await navigator.credentials.get({ publicKey: opts })) as PublicKeyCredential | null } catch (e) { translate(e) }
  if (!cred) throw new PasskeyCancelled()
  const r = cred.response as AuthenticatorAssertionResponse
  return {
    id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type,
    response: {
      clientDataJSON: bufToB64u(r.clientDataJSON), authenticatorData: bufToB64u(r.authenticatorData),
      signature: bufToB64u(r.signature), userHandle: bufToB64u(r.userHandle),
    },
  }
}

/** Create a passkey on this device. Returns the body for `/account/passkey/register/verify`. */
export async function createAttestation(publicKey: Json): Promise<Json> {
  const user = isObj(publicKey.user) ? publicKey.user : {}
  const rp = isObj(publicKey.rp) ? publicKey.rp : {}
  const opts: PublicKeyCredentialCreationOptions = {
    challenge: b64uToBuf(String(publicKey.challenge)),
    rp: { name: String(rp.name ?? 'Tayanch'), ...(typeof rp.id === 'string' ? { id: rp.id } : {}) },
    user: { id: b64uToBuf(String(user.id)), name: String(user.name ?? ''), displayName: String(user.displayName ?? user.name ?? '') },
    pubKeyCredParams: (Array.isArray(publicKey.pubKeyCredParams) ? publicKey.pubKeyCredParams : [{ type: 'public-key', alg: -7 }, { type: 'public-key', alg: -257 }]) as PublicKeyCredentialParameters[],
    authenticatorSelection: isObj(publicKey.authenticatorSelection) ? (publicKey.authenticatorSelection as AuthenticatorSelectionCriteria) : undefined,
    timeout: typeof publicKey.timeout === 'number' ? publicKey.timeout : 60000,
    attestation: 'none',
    excludeCredentials: creds(publicKey.excludeCredentials),
  }
  let cred: PublicKeyCredential | null
  try { cred = (await navigator.credentials.create({ publicKey: opts })) as PublicKeyCredential | null } catch (e) { translate(e) }
  if (!cred) throw new PasskeyCancelled()
  const r = cred.response as AuthenticatorAttestationResponse
  return {
    id: cred.id, rawId: bufToB64u(cred.rawId), type: cred.type,
    response: { clientDataJSON: bufToB64u(r.clientDataJSON), attestationObject: bufToB64u(r.attestationObject) },
  }
}
