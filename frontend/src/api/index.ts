import type { Api } from './contract'
import { mockApi } from './mock'
import { httpApi } from './client'

/* Live API by default in prod/staging, the mock in dev. Set VITE_USE_MOCK
   (true/false) to flip either one. */
const flag = import.meta.env.VITE_USE_MOCK
const useMock = flag === undefined ? import.meta.env.DEV : flag === 'true'

export const api: Api = useMock ? mockApi : httpApi

