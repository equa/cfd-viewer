import { useEffect, useRef, useState } from 'react'
import { ActionIcon, Divider, Group, Slider, Text, Tooltip } from '@mantine/core'
import { IconPlayerPause, IconPlayerPlay, IconRefresh } from '@tabler/icons-react'
import { VIEW_BUTTONS } from '../scene/state.js'

/*
 * The floating strip over the 3D view: camera presets left, time controls right.
 *
 * Both act on the whole scene rather than on one representation, so they live
 * outside the per-tool pane -- and floating over the canvas keeps them one
 * glance from the result they change. Same reasoning, same layout as the Trame
 * bottom bar.
 *
 * The time slider commits on release here, where the Trame one stayed live.
 * That was a known wart there ("a mouse-drag of it on a big case would still
 * flood"); Mantine's onChangeEnd makes fixing it free, and the part cache means
 * stepping back to a step you have already seen costs nothing at all.
 */

export function BottomBar({ meta, request, setRequest, onView, onRefreshTimes }) {
  const times = meta?.times || []
  const last = Math.max(times.length - 1, 0)
  const index = Math.min(request.time_index ?? last, last)
  const [draft, setDraft] = useState(index)
  const [playing, setPlaying] = useState(false)
  const timer = useRef(null)

  useEffect(() => { setDraft(index) }, [index])

  // Playback steps the index directly, one step per tick. The cache is what
  // makes a second pass through a loop smooth: every step after the first is
  // already decoded and on the GPU.
  useEffect(() => {
    if (!playing || times.length < 2) return undefined
    timer.current = setInterval(() => {
      setRequest((r) => ({ ...r, time_index: ((r.time_index ?? 0) + 1) % times.length }))
    }, 600)
    return () => clearInterval(timer.current)
  }, [playing, times.length, setRequest])

  const label = times.length ? `${Number(times[draft] ?? 0)}` : '0'

  return (
    <div className="bottombar">
      <Group gap={2} wrap="nowrap">
        {VIEW_BUTTONS.map(([text, direction]) => (
          <button
            key={direction}
            type="button"
            className="viewbtn"
            onClick={() => onView(direction)}
            data-ctl={`view-${direction.replace('+', 'p').replace('-', 'm')}`}
          >
            {text}
          </button>
        ))}
      </Group>

      <Divider orientation="vertical" mx={6} />

      <Tooltip label={playing ? 'Pause' : 'Play through time'} openDelay={600}>
        <ActionIcon
          variant="subtle"
          size="sm"
          disabled={times.length < 2}
          onClick={() => setPlaying((p) => !p)}
          data-ctl="play"
          aria-label={playing ? 'Pause' : 'Play'}
        >
          {playing
            ? <IconPlayerPause size={15} stroke={1.8} />
            : <IconPlayerPlay size={15} stroke={1.8} />}
        </ActionIcon>
      </Tooltip>
      <Slider
        size="sm"
        thumbSize={14}
        style={{ width: 170 }}
        min={0}
        max={last}
        step={1}
        label={null}
        disabled={times.length < 2}
        value={draft}
        onChange={setDraft}
        onChangeEnd={(v) => setRequest({ time_index: v })}
        data-ctl="time"
      />
      <Text size="xs" c="dimmed" ff="monospace" style={{ minWidth: 76 }} data-ctl="time-label">
        t = {label}
      </Text>
      {/* A running solve keeps writing time steps, so re-scan and pick them up
          without reopening the case. */}
      <Tooltip label="Re-scan for new time steps" openDelay={600}>
        <ActionIcon
          variant="subtle"
          size="sm"
          onClick={onRefreshTimes}
          data-ctl="refresh-times"
          aria-label="Refresh time steps"
        >
          <IconRefresh size={15} stroke={1.8} />
        </ActionIcon>
      </Tooltip>
    </div>
  )
}
