import { useEffect, useMemo, useRef, useState } from 'react'
import { Badge, ColorInput, Group, NumberInput, Slider, Switch, Text } from '@mantine/core'

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

/*
 * A number input the mouse wheel drives -- but ONLY while it has focus.
 *
 * Mantine does not wire the wheel up, and doing it unconditionally would be
 * worse than not having it: these inputs live in a scrolling side pane, so a
 * wheel gesture aimed at the pane would silently edit whichever field happened
 * to be under the pointer. Requiring focus makes it deliberate -- click the
 * field, then wheel -- which is how a spinner is expected to behave.
 *
 * `passive: false` is required: the listener has to preventDefault to stop the
 * pane scrolling underneath, and wheel listeners default to passive, where
 * preventDefault is ignored.
 */
export function NumberField({ value, onChange, step = 1, ...rest }) {
  const ref = useRef(null)
  const state = useRef({ value, onChange, step })
  state.current = { value, onChange, step }

  useEffect(() => {
    const el = ref.current
    if (!el) return undefined
    const onWheel = (event) => {
      if (document.activeElement !== el) return
      event.preventDefault()
      const { value: current, onChange: commit, step: by } = state.current
      const base = Number(current)
      if (!Number.isFinite(base)) return
      const next = base + (event.deltaY < 0 ? by : -by)
      // Round to the step's own precision, or float noise turns 2.2 into
      // 2.2000000000000002 in a field the user is reading.
      const decimals = (String(by).split('.')[1] || '').length
      commit(Number(next.toFixed(decimals)))
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  return <NumberInput {...rest} ref={ref} value={value} step={step} onChange={onChange} />
}

/*
 * "Colour by field" plus the solid colour it falls back to, as one control.
 *
 * Every field-coloured part wants the same pair, so it lives here rather than
 * being repeated in five panels. Both halves are shader uniforms: instant.
 */
export function ColourBy({ ctl, colored, solid, onChange, defaultSolid = '#b8c0cc' }) {
  return (
    <>
      <Switch
        size="xs"
        label={(
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            Colour by field<Tag kind="client" />
          </span>
        )}
        checked={colored}
        onChange={(e) => onChange({ colored: e.currentTarget.checked })}
        data-ctl={`${ctl}-colored`}
      />
      {colored ? null : (
        <ColorInput
          size="xs"
          mt={6}
          withEyeDropper={false}
          format="hex"
          swatches={['#b8c0cc', '#ffffff', '#8ab4f8', '#f28b82', '#81c995', '#fdd663', '#4a5058']}
          value={solid || defaultSolid}
          onChange={(v) => onChange({ solid: v })}
          data-ctl={`${ctl}-solid`}
        />
      )}
    </>
  )
}

/*
 * A "nice" step for a field spanning `span` -- about 1/100th of it, snapped to
 * 1/2/5 x a power of ten.
 *
 * Needed because these inputs are now wheel-driven, and a fixed step of 1 is
 * wrong for almost every field here: it is uselessly coarse on |U| (0..0.23)
 * and uselessly fine on p. It also sets the spinner arrows, so it matters even
 * without the wheel.
 */
export function stepFor(span, fallback = 0.1) {
  const magnitude = Math.abs(span)
  if (!Number.isFinite(magnitude) || magnitude <= 0) return fallback
  const raw = magnitude / 100
  const power = 10 ** Math.floor(Math.log10(raw))
  const scaled = raw / power
  const snapped = scaled >= 5 ? 5 : scaled >= 2 ? 2 : 1
  return snapped * power
}

/* Shared sizing, so every panel's inputs line up without repeating props. */
export const INPUT = { size: 'xs' }
export const SELECT = { size: 'xs', comboboxProps: { withinPortal: true } }
