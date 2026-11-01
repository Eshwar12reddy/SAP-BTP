/** Shapes returned by the FastAPI backend (see backend/app/schemas). */

export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export type MessageRole = 'user' | 'assistant' | 'system'

export interface Message {
  id: string
  conversation_id: string
  role: MessageRole
  content: string
  client_message_id: string | null
  created_at: string
}

export interface Page<T> {
  items: T[]
  has_more: boolean
}

export interface ApiErrorBody {
  error: {
    code: string
    message: string
    request_id: string | null
  }
}
