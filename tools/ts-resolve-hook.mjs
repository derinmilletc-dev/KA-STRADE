// Testlerde kök dizindeki .ts modüllerinin uzantısız içe aktarmalarını (vite gibi) çözer.
import {existsSync} from 'node:fs'
import {fileURLToPath} from 'node:url'
export async function resolve(specifier, context, next) {
  if (/^\.\.?\//.test(specifier) && !/\.[a-z]+$/i.test(specifier) && context.parentURL) {
    const url = new URL(`${specifier}.ts`, context.parentURL)
    if (existsSync(fileURLToPath(url))) return next(url.href, context)
  }
  return next(specifier, context)
}
