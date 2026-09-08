import { useEffect, useMemo, useState } from 'react'
import { Badge, Group, Slider, Text } from '@mantine/core'

/*
 * The control vocabulary shared by every panel.
 *
 * Two things here carry over from the Trame app and are worth keeping:
 *
 * `Tag` — every control is labelled `server` or `client`, so the cost of using
 * it is visible while you use it. This caught more than one wrong assumption
 * during the spike and it stays.
 *
 * `useDeferred` — the Apply-button mechanism. The Trame app called these
 * `_APPLY_GROUPS`: a group of inputs edits drafts and reaches the real state
 * only when Apply is pressed, so a heavy case is not re-extracted per keystroke
 * or per toggle. Streamline tuning and the plane's numeric X/Y/Z fields still
 * need exactly that; the colour options mostly do not any more, because bands
 * and range became shader uniforms.
 */

export function Tag({ kind }) {
  return (
    <Badge
      size="xs"
      variant="light"
      color={kind === 'server' ? 'orange' : 'teal'}
      style={{ textTransform: 'lowercase', fontWeight: 500 }}
    >
      {kind}
    </Badge>
  )
}

export function Head({ children, kind }) {
  return (
    <Group justify="space-between" gap="xs" mb={6} wrap="nowrap">
      <Text size="xs" fw={600} tt="uppercase" c="dimmed" style={{ letterSpacing: '.04em' }}>
        {children}
      </Text>
      {kind ? <Tag kind={kind} /> : null}
    </Group>
  )
}

/*
 * A labelled slider that shows its live value and commits on release.
 *
 * `onChangeEnd` is Mantine's release event, which is exactly the contract the
 * Trame app built by hand (VSlider `@end` writing a `<name>_draft` mirror into
 * the real state var) and the contract the spike had to hand-roll on a native
 * `change` listener, because React maps both onChange and onInput to the
 * `input` event and offers no "user let go". Getting it from the component is
 * the single clearest win from adopting Mantine here.
 *
 * `live` sliders (appearance: opacity, lighting) commit on every tick, because
 * they cost nothing. `defer` sliders belong to an Apply group and never
 * self-commit -- they only move their draft.
 */
export function LabelledSlider({
  label, value, onChange, onCommit, onPreview, unit = '', precision = 2,
  live = false, ...rest
}) {
  const [draft, setDraft] = useState(value)
  useEffect(() => { setDraft(value) }, [value])
  const shown = typeof draft === 'number' ? draft.toFixed(precision) : draft
  return (
    <div style={{ marginBottom: 10 }}>
      <Group justify="space-between" gap={4} mb={2} wrap="nowrap">
        <Text size="xs" c="dimmed">{label}</Text>
        <Text size="xs" ff="monospace">{shown}{unit}</Text>
      </Group>
      <Slider
        {...rest}
        value={draft}
        size="sm"
        thumbSize={14}
        label={null}
        onChange={(v) => {
          setDraft(v)
          // `onPreview` fires on every tick of the drag whether or not the
          // slider is live. That distinction matters: a deferred slider still
          // wants to show you something while you drag it -- the cut plane's
          // red frame is drawn from here, and it is the whole reason dragging
          // feels immediate while the extraction waits for the release.
          onPreview?.(v)
          if (live) onChange?.(v)
        }}
        onChangeEnd={(v) => {
          setDraft(v)
          if (live) onChange?.(v)
          else onCommit?.(v)
        }}
      />
    </div>
  )
}

/*
 * An Apply group: inputs edit `draft`, nothing happens until `apply()`.
 *
 * `dirty` drives the Apply button's enabled state, which is a small but real
 * improvement on the Trame version -- there, Apply was always live and pressing
 * it with nothing changed still paid for a full re-extraction.
 */
export function useDeferred(committed, onApply) {
  // Resync on a *value* change, not on every render: `committed` is a fresh
  // object each time, so keying the effect on the object itself would loop.
  const fingerprint = JSON.stringify(committed)
  const [draft, setDraft] = useState(committed)
  useEffect(() => { setDraft(JSON.parse(fingerprint)) }, [fingerprint])

  const dirty = useMemo(
    () => JSON.stringify(draft) !== fingerprint,
    [draft, fingerprint],
  )

  return {
    draft,
    dirty,
    set: (patch) => setDraft((d) => ({ ...d, ...patch })),
    apply: () => { if (dirty) onApply(draft) },
    reset: () => setDraft(JSON.parse(fingerprint)),
  }
}

/* Shared sizing, so every panel's inputs line up without repeating props. */
export const INPUT = { size: 'xs' }
export const SELECT = { size: 'xs', comboboxProps: { withinPortal: true } }
