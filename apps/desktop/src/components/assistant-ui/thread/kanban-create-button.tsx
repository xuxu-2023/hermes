import { useStore } from '@nanostores/react'
import { type FC, useCallback } from 'react'

import { useI18n } from '@/i18n'
import { Codicon } from '@/components/ui/codicon'
import {
  DropdownMenuItem
} from '@/components/ui/dropdown-menu'
import { notify, notifyError } from '@/store/notifications'
import { $activeSessionId } from '@/store/session'
import { $activeGatewayProfile } from '@/store/profile'
import { $todosBySession } from '@/store/todos'

export const KanbanCreateTaskItem: FC<{ getMessageText: () => string; messageId: string }> = ({
  getMessageText,
  messageId
}) => {
  const activeSessionId = useStore($activeSessionId)
  const activeProfileId = useStore($activeGatewayProfile)

  const handleCreateKanbanTask = useCallback(async () => {
    const text = getMessageText()
    if (!text.trim()) return
    try {
      const boards = await window.hermesDesktop.kanban.boards()
      const boardId = (boards[0]?.id) || 'default'
      await window.hermesDesktop.kanban.createTask({
        boardId,
        title: text.slice(0, 120),
        description: text,
        source: 'chat',
        sessionId: activeSessionId ?? undefined,
        profileId: activeProfileId,
        messageId,
        assigneeType: 'user',
        assigneeLabel: 'You',
        syncMode: 'manual'
      })
      notify({ message: 'Kanban task created' })
    } catch {
      notifyError(new Error('Failed to create kanban task'), 'Failed to create kanban task')
    }
  }, [getMessageText, activeSessionId, activeProfileId, messageId])

  return (
    <DropdownMenuItem onSelect={handleCreateKanbanTask}>
      <Codicon name="project" size="0.875rem" />
      Create Kanban Task
    </DropdownMenuItem>
  )
}

export const KanbanSendPlanItem: FC<{ activeSessionId?: string | null }> = ({ activeSessionId: explicitSessionId }) => {
  const storeSessionId = useStore($activeSessionId)
  const sessionId = explicitSessionId ?? storeSessionId
  const activeProfileId = useStore($activeGatewayProfile)
  const todosBySession = useStore($todosBySession)
  const todos = sessionId ? (todosBySession[sessionId] ?? []) : []
  const pendingTodos = todos.filter(t => t.status === 'pending' || t.status === 'in_progress')

  const handleSendPlanToKanban = useCallback(async () => {
    if (pendingTodos.length === 0) return
    try {
      const boards = await window.hermesDesktop.kanban.boards()
      const boardId = (boards[0]?.id) || 'default'
      let created = 0
      for (const todo of pendingTodos) {
        await window.hermesDesktop.kanban.createTask({
          boardId,
          title: todo.content.slice(0, 120),
          description: todo.content,
          source: 'agent',
          status: todo.status === 'in_progress' ? 'running' : 'todo',
          sessionId: sessionId ?? undefined,
          profileId: activeProfileId,
          externalTaskId: todo.id,
          externalTaskKind: 'agent_plan_item',
          assigneeType: 'agent',
          assigneeLabel: 'Hermes',
          syncMode: 'linked'
        })
        created++
      }
      notify({ message: `${created} Kanban tasks created from plan` })
    } catch {
      notifyError(new Error('Failed to send plan to kanban'), 'Failed to send plan to kanban')
    }
  }, [pendingTodos, sessionId, activeProfileId])

  if (pendingTodos.length === 0) return null

  return (
    <DropdownMenuItem onSelect={handleSendPlanToKanban}>
      <Codicon name="checklist" size="0.875rem" />
      Send plan to Kanban ({pendingTodos.length})
    </DropdownMenuItem>
  )
}
