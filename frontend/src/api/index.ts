import type { Api } from './contract'
import { mockApi } from './mock'
import { httpApi } from './client'

/* Default to the mock so the app runs with no backend; opt into the real one
   with VITE_USE_MOCK=false. */
const useMock = import.meta.env.VITE_USE_MOCK !== 'false'

export const api: Api = useMock ? mockApi : httpApi

