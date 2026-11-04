import ReactMarkdown, { type Components, type UrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'

const SAFE_PROTOCOLS = new Set(['http:', 'https:', 'mailto:'])

/** Keep absolute http(s) and mailto URLs only; javascript:, data:, relative paths etc. are dropped. */
export const safeUrl: UrlTransform = (url) => {
  try {
    return SAFE_PROTOCOLS.has(new URL(url).protocol) ? url : null
  } catch {
    return null
  }
}

const components: Components = {
  a: ({ href, children }) =>
    href ? (
      <a href={href} target="_blank" rel="noopener noreferrer nofollow">
        {children}
      </a>
    ) : (
      <span>{children}</span>
    ),
  // Remote images are never loaded (tracking, data exfiltration); the alt text stays readable.
  img: ({ alt }) => (alt ? <span>[{alt}]</span> : null),
  table: ({ children }) => (
    <div className="markdown__table">
      <table>{children}</table>
    </div>
  ),
}

/**
 * Renders assistant Markdown as React elements. Raw HTML is skipped, so nothing in a reply
 * can inject markup or scripts; there is no dangerouslySetInnerHTML anywhere.
 */
export function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml urlTransform={safeUrl} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  )
}
