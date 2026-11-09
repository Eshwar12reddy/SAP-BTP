import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from '../App'
import { api } from '../services/api'
import type { KnowledgeSearchResponse, KnowledgeSource } from '../types/api'
import { FakeBackend } from '../test/fakeBackend'

const source: KnowledgeSource = {
  id: 'source-1', title: 'Runbook', description: 'Operations notes', tags: ['ops'], scope_type: 'global',
  btp_account_id: null, btp_environment_id: null, owner_id: 'user-1', created_by: 'user-1', status: 'active', revision: 1,
  created_at: '2026-11-01T10:00:00Z', updated_at: '2026-11-01T10:00:00Z',
  active_version: { id: 'version-1', source_id: 'source-1', version_number: 1, original_filename: 'runbook.md', media_type: 'text/markdown', size_bytes: 120, sha256: 'redacted-test-checksum', uploaded_by: 'user-1', validation_status: 'validated', validated_at: '2026-11-01T10:00:00Z', change_note: null, created_at: '2026-11-01T10:00:00Z', is_active: true },
}

const result: KnowledgeSearchResponse = {
  query: 'restart', retrieval_mode: 'lexical', semantic_status: 'disabled_by_configuration', reranking_status: 'disabled_model_not_approved', indexing_status: 'ready', answer_mode: 'extractive_only',
  items: [{ citation: { citation_id: 'c1', source_id: 'source-1', source_title: 'Runbook', source_version_id: 'version-1', version_number: 1, page_start: null, page_end: null, section_heading: 'Restart', parent_id: 'p1', child_id: 'c1', download_url: '/api/v1/knowledge/sources/source-1/versions/version-1/download' }, excerpt: 'Restart the application after checking its state.', parent_context: null, lexical_rank: 1, vector_rank: null, fused_score: 1 }],
}

describe('KnowledgePage', () => {
  it('lists sources and performs extractive search', async () => {
    const backend = new FakeBackend().install()
    backend.addUser('manager@example.com', ['knowledge_manager'], { name: 'Knowledge Manager' })
    backend.signIn('manager@example.com')
    vi.spyOn(api, 'listKnowledgeSources').mockResolvedValue({ items: [source], has_more: false })
    vi.spyOn(api, 'myScopes').mockResolvedValue({ grants: [], btp_read: { accounts: [], environments: [] } })
    vi.spyOn(api, 'searchKnowledge').mockResolvedValue(result)
    window.history.replaceState(null, '', '/knowledge')
    render(<App />)

    expect(await screen.findByText('Runbook')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Question or keywords'), 'restart')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(await screen.findByText('Restart the application after checking its state.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open source file' })).toHaveAttribute('href', result.items[0].citation.download_url)
  })

  it('shows upload controls only to a knowledge manager and sends a File', async () => {
    const backend = new FakeBackend().install()
    backend.addUser('manager@example.com', ['knowledge_manager'], { name: 'Knowledge Manager' })
    backend.signIn('manager@example.com')
    vi.spyOn(api, 'listKnowledgeSources').mockResolvedValue({ items: [], has_more: false })
    vi.spyOn(api, 'myScopes').mockResolvedValue({ grants: [], btp_read: { accounts: [], environments: [] } })
    const create = vi.spyOn(api, 'createKnowledgeSource').mockResolvedValue(source)
    window.history.replaceState(null, '', '/knowledge')
    render(<App />)

    const file = new File(['# Runbook'], 'runbook.md', { type: 'text/markdown' })
    await userEvent.type(await screen.findByLabelText('Title'), 'Runbook')
    await userEvent.upload(document.querySelector('input[type="file"]') as HTMLInputElement, file)
    fireEvent.submit(screen.getByRole('form', { name: 'Upload knowledge source' }))
    await waitFor(() => expect(create).toHaveBeenCalled())
    expect(await screen.findByRole('status')).toHaveTextContent('uploaded and validated')
    expect(create).toHaveBeenCalledWith(expect.objectContaining({ title: 'Runbook', file }))
  })
})
