import { resolve } from 'node:path'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Search and link-preview files that depend on the public site URL.
 *
 * With VITE_SITE_URL (e.g. https://meetpoint.app) set at build time:
 *  - adds <link rel="canonical">, og:url, and makes og:image / twitter:image absolute
 *  - writes sitemap.xml and points robots.txt at it
 * Without it, robots.txt is still written (no Sitemap line) and nothing else changes.
 *
 * The landing page (public/index.html, static) and the app (/plan) are listed;
 * meetup links (/plan?g=...) are private and noindex.
 */
function seoFiles(siteUrl) {
  const site = (siteUrl || '').replace(/\/+$/, '')
  return {
    name: 'meetpoint-seo-files',
    // Only the app page (plan/index.html) goes through Vite; the landing page is static.
    transformIndexHtml(html) {
      if (!site) return html
      const abs = (path) => `${site}${path}`
      return html
        .replace(/(property="og:image" content=")\/og-image\.png/, `$1${abs('/og-image.png')}`)
        .replace(/(name="twitter:image" content=")\/og-image\.png/, `$1${abs('/og-image.png')}`)
        .replace(
          '</head>',
          `    <link rel="canonical" href="${abs('/plan')}" />\n    <meta property="og:url" content="${abs('/plan')}" />\n  </head>`,
        )
    },
    generateBundle() {
      const robots = [
        '# MeetPoint',
        '# The landing page and /plan are public. Meetup links (/plan?g=...) carry a noindex header and tag.',
        'User-agent: *',
        'Allow: /',
        'Disallow: /api/',
        site ? `\nSitemap: ${site}/sitemap.xml` : '',
        '',
      ].join('\n')
      this.emitFile({ type: 'asset', fileName: 'robots.txt', source: robots })

      if (site) {
        const today = new Date().toISOString().slice(0, 10)
        const sitemap =
          '<?xml version="1.0" encoding="UTF-8"?>\n' +
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
          `  <url><loc>${site}/</loc><lastmod>${today}</lastmod></url>\n` +
          `  <url><loc>${site}/plan</loc><lastmod>${today}</lastmod></url>\n` +
          '</urlset>\n'
        this.emitFile({ type: 'asset', fileName: 'sitemap.xml', source: sitemap })
      }
    },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.VITE_API_URL || 'http://127.0.0.1:8000'
  return {
    plugins: [react(), seoFiles(env.VITE_SITE_URL)],
    // The landing page is public/index.html (static, copied as-is with its kit in public/assets);
    // the app is plan/index.html. Vite's own bundles go to /static so they never mix with the kit.
    build: {
      assetsDir: 'static',
      rollupOptions: { input: { plan: resolve(__dirname, 'plan/index.html') } },
    },
    server: { port: 5173, proxy: { '/api': { target, changeOrigin: true } } },
    preview: { port: 4173, proxy: { '/api': { target, changeOrigin: true } } },
  }
})
