import { useMemo, useState } from 'react'
import {
  Alert, Badge, Button, Group, ScrollArea, Table, Text, TextInput, Title,
} from '@mantine/core'
import { IconAlertTriangle, IconRefresh, IconSearch } from '@tabler/icons-react'

/*
 * What the viewer shows before a case is open: every case the server can see,
 * filterable, one click to open.
 *
 * It appears when the page has no ?case= and the server lists more (or fewer)
 * than one case, or when ?case= named a case that does not exist -- then with
 * the server's reason on top. Embedded in the cockpit (?case= given) it never
 * shows.
 *
 * Costs nothing on the server side: it reads /api/config and /api/cases only,
 * both of which open no case, and moves no part signature (openspec
 * standalone-viewer, start-screen spec). Opening a case goes through the
 * normal /api/meta path in App.
 *
 * It OVERLAYS the app rather than replacing it, so the stage -- and with it the
 * Viewer's WebGL context -- stays mounted from the first commit (CLAUDE.md,
 * "The React tree shape must not change once the Viewer exists").
 *
 * Laid out as a header bar plus one list so it maps onto the equa-ui shell
 * (TopBar + a card list) when the client moves to @equa/ui.
 */
export function StartScreen({ entries, config, notice, loading, onOpen, onRefresh }) {
  const [filter, setFilter] = useState('')

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    if (!needle) return entries
    return entries.filter((e) => [e.label, e.name, e.path]
      .some((v) => v && v.toLowerCase().includes(needle)))
  }, [entries, filter])

  return (
    <div className="start" data-ctl="start-screen">
      <Group className="start-head" gap="xs" wrap="nowrap">
        <Text fw={700} size="sm" className="brand">FoamViz</Text>
        <Text size="xs" c="dimmed">
          {config ? `${config.version} · ${config.mode} mode` : ''}
        </Text>
      </Group>

      <div className="start-body">
        <Group justify="space-between" align="flex-end" mb="sm" wrap="nowrap">
          <div>
            <Title order={4}>Open a case</Title>
            <Text size="xs" c="dimmed">
              {loading ? 'Looking for cases…'
                : `${entries.length} case${entries.length === 1 ? '' : 's'} found`}
            </Text>
          </div>
          <Group gap="xs" wrap="nowrap">
            <TextInput
              size="xs"
              placeholder="Filter by name or path"
              leftSection={<IconSearch size={14} stroke={1.8} />}
              value={filter}
              onChange={(e) => setFilter(e.currentTarget.value)}
              disabled={!entries.length}
              data-ctl="case-filter"
              w={240}
            />
            <Button
              size="xs"
              variant="light"
              leftSection={<IconRefresh size={14} stroke={1.8} />}
              onClick={onRefresh}
              loading={loading}
              data-ctl="case-refresh"
            >
              Refresh
            </Button>
          </Group>
        </Group>

        {notice && (
          <Alert
            color="orange"
            variant="light"
            icon={<IconAlertTriangle size={16} />}
            mb="sm"
            data-ctl="case-notice"
          >
            {notice}
          </Alert>
        )}

        {!loading && !entries.length ? (
          <Text size="sm" c="dimmed" data-ctl="case-empty">
            No case found. A case is a directory containing
            {' '}<code>system/controlDict</code>; cases written later appear on Refresh.
          </Text>
        ) : (
          <ScrollArea className="start-list" type="auto">
            <Table highlightOnHover verticalSpacing={6} fz="sm">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Case</Table.Th>
                  <Table.Th>Path</Table.Th>
                  <Table.Th ta="right">Cells</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {shown.map((e) => (
                  <Table.Tr
                    key={e.id}
                    className="start-row"
                    onClick={() => onOpen(e.id)}
                    data-ctl="case-row"
                    data-case-id={e.id}
                  >
                    <Table.Td>
                      <Text size="sm" fw={600}>{e.label}</Text>
                      {e.label !== e.name && <Text size="xs" c="dimmed">{e.name}</Text>}
                    </Table.Td>
                    <Table.Td><Text size="xs" c="dimmed">{e.path}</Text></Table.Td>
                    <Table.Td ta="right">
                      {e.cells == null ? '—' : e.cells.toLocaleString()}
                    </Table.Td>
                    <Table.Td ta="right">
                      {e.decomposed && <Badge size="xs" variant="light">decomposed</Badge>}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            {entries.length > 0 && !shown.length && (
              <Text size="sm" c="dimmed" mt="sm">Nothing matches “{filter}”.</Text>
            )}
          </ScrollArea>
        )}
      </div>
    </div>
  )
}
