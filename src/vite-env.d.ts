/// <reference types="vite/client" />

// Common image imports Vite handles at build time — declare here so
// TypeScript doesn't complain about `import x from '@/assets/foo.jpg'`.
declare module '*.jpg'  { const src: string; export default src }
declare module '*.jpeg' { const src: string; export default src }
declare module '*.png'  { const src: string; export default src }
declare module '*.svg'  { const src: string; export default src }
declare module '*.webp' { const src: string; export default src }
