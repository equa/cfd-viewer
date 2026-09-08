import {
  ActionIcon, Button, Divider, Group, NumberInput, Popover, Select, Switch, Text, Tooltip,
} from '@mantine/core'
import {
  IconAdjustments, IconArrowsHorizontal, IconCamera,
} from '@tabler/icons-react'
import { COMPONENTS } from '../scene/state.js'
import { Head, SELECT, Tag, useDeferred } from './controls.jsx'

/*
 * The top bar: global colour settings, because they affect every part.
 *
 * Layout inherited from the Trame app (rearranged there 2026-08-21, and it was
 * the right call): the essentials inline -- Field, Component, Colour map, Auto
 * range, Rescale -- and the rest behind an "Options" popover so the bar stays
 * legible.
 *
 * What changed, and it is the point of the whole exercise: the popover's Apply
 * button now covers only the two settings that still cost an extraction.
 * Bands, min/max and the opacity ramp became shader uniforms, so they are live
 * and instant -- the Trame app had to defer them behind Apply because each one
 * rebuilt a vtkColorTransferFunction and re-serialised it to the browser. The
 * `server`/`client` tags in the popover say which is which.
 */

const PRESET_LABEL = {
  coolwarm: 'Cool → warm',
  viridis: 'Viridis',
  plasma: 'Plasma',
  inferno: 'Inferno',
  magma: 'Magma',
  jet: 'Jet',
  turbo: 'Turbo',
  rainbow: 'Rainbow',
}

export function TopBar({
  meta, request, setRequest, appearance, setAppearance, onRescale, onScreenshot,
}) {
  const fields = meta ? Object.keys(meta.fields).sort() : []
  const componentEnabled = !!meta && meta.fields[request.field] === 3

  // Only the two genuinely server-side colour options are deferred now.
  const options = useDeferred(
    { robust: request.robust, cell_data: request.cell_data },
    (draft) => setRequest(draft),
  )

  return (
    <Group className="topbar" gap="xs" wrap="nowrap">
      <Text fw={700} size="sm" className="brand">FoamViz</Text>
      <Divider orientation="vertical" />

      <Select
        {...SELECT}
        label={null}
        placeholder="Field"
        data={fields}
        value={request.field || null}
        onChange={(v) => v && setRequest({ field: v })}
        style={{ width: 130 }}
        allowDeselect={false}
        data-ctl="field"
      />
      <Select
        {...SELECT}
        data={COMPONENTS.map((c) => ({ value: c.value, label: c.label }))}
        value={request.component}
        onChange={(v) => v && setRequest({ component: v })}
        disabled={!componentEnabled}
        style={{ width: 130 }}
        allowDeselect={false}
        data-ctl="component"
      />
      <Select
        {...SELECT}
        data={(meta?.presets || []).map((p) => ({ value: p, label: PRESET_LABEL[p] || p }))}
        value={appearance.preset}
        onChange={(v) => v && setAppearance({ preset: v })}
        style={{ width: 140 }}
        allowDeselect={false}
        data-ctl="preset"
      />

      {/* Auto range sits immediately left of its manual counterpart, Rescale --
          the same pairing as in the Trame top bar. */}
      <Tooltip label="Track the data range as field or time changes" openDelay={600}>
        <Switch
          size="xs"
          label="Auto range"
          checked={appearance.autoRange}
          onChange={(e) => setAppearance({ autoRange: e.currentTarget.checked })}
          data-ctl="auto-range"
        />
      </Tooltip>
      <Button
        size="xs"
        variant="light"
        leftSection={<IconArrowsHorizontal size={14} stroke={1.8} />}
        onClick={onRescale}
        data-ctl="rescale"
      >
        Rescale
      </Button>

      <Popover width={280} position="bottom-start" withArrow shadow="md">
        <Popover.Target>
          <Button
            size="xs"
            variant="subtle"
            leftSection={<IconAdjustments size={14} stroke={1.8} />}
            data-ctl="options"
          >
            Options
          </Button>
        </Popover.Target>
        <Popover.Dropdown>
          {/* Instant: uniforms. No Apply, no round trip -- this is the half of
              the old Options popover that stopped needing one. */}
          <Head kind="client">Colour mapping</Head>
          <NumberInput
            size="xs"
            label="Bands"
            description="0 = smooth"
            min={0}
            max={256}
            step={1}
            value={appearance.bands}
            onChange={(v) => setAppearance({ bands: Number(v) || 0 })}
            data-ctl="bands"
          />
          <Group grow gap="xs" mt="xs">
            <NumberInput
              size="xs"
              label="Min"
              value={appearance.range[0]}
              decimalScale={4}
              onChange={(v) => setAppearance({
                range: [Number(v), appearance.range[1]], autoRange: false,
              })}
              data-ctl="range-min"
            />
            <NumberInput
              size="xs"
              label="Max"
              value={appearance.range[1]}
              decimalScale={4}
              onChange={(v) => setAppearance({
                range: [appearance.range[0], Number(v)], autoRange: false,
              })}
              data-ctl="range-max"
            />
          </Group>
          <Switch
            mt="sm"
            size="xs"
            label="Opacity by value"
            description="Low values fade out (linear)"
            checked={appearance.opacityMap}
            onChange={(e) => setAppearance({ opacityMap: e.currentTarget.checked })}
            data-ctl="opacity-map"
          />

          <Divider my="sm" />

          {/* Deferred: these two re-read or re-bake the scalars, so they keep
              the Apply button the Trame app gave the whole group. */}
          <Head kind="server">Sampling</Head>
          <Switch
            size="xs"
            label="Robust range (1–99%)"
            checked={options.draft.robust}
            onChange={(e) => options.set({ robust: e.currentTarget.checked })}
            data-ctl="robust-range"
          />
          <Switch
            mt={6}
            size="xs"
            label="True cell values"
            description="Flat per cell, not point-interpolated"
            checked={options.draft.cell_data}
            onChange={(e) => options.set({ cell_data: e.currentTarget.checked })}
            data-ctl="cell-data"
          />
          <Button
            fullWidth
            mt="sm"
            size="xs"
            variant="light"
            disabled={!options.dirty}
            onClick={options.apply}
            data-ctl="apply-options"
          >
            Apply
          </Button>
        </Popover.Dropdown>
      </Popover>

      <div style={{ flex: 1 }} />

      <Text size="xs" c="dimmed" className="case-note">
        {meta ? `${meta.case} · ${meta.cells.toLocaleString()} cells` : 'loading…'}
        {meta?.decomposed ? ' · decomposed' : ''}
      </Text>
      <Tooltip label="Save a PNG of the 3D view" openDelay={600}>
        <ActionIcon variant="subtle" size="md" onClick={onScreenshot} data-ctl="screenshot">
          <IconCamera size={17} stroke={1.6} />
        </ActionIcon>
      </Tooltip>
    </Group>
  )
}
