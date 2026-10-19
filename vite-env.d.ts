/// <reference types="vite/client" />

interface ImportMetaEnv {
	readonly VITE_BUILD_COMMIT: string
	readonly VITE_ADMIN_ORIGIN?: string
	readonly VITE_CUSTOMER_ORIGIN?: string
}
