import { Accordion, ActionIcon, Divider, Group, Select, Text, Tooltip } from '@mantine/core'
import {
  IconArrowUpRight, IconBlur, IconBulb, IconCube, IconEye, IconEyeOff,
  IconHome, IconSquare, IconVector,
} from '@tabler/icons-react'
import { TOOLS, TOOL_PART } from '../scene/state.js'
import { Head, LabelledSlider, SELECT } from './controls.jsx'
import {
  BoundaryTool, ContourTool, CutPlaneTool, GeometryTool, GlyphTool, StreamTool,
} from './tools.jsx'

/*
 * The side pane: case at the top, then the vertical tool stack, then the
 * selected tool's settings, then Lighting collapsed at the bottom.
 *
 * Straight from the Trame layout, including the division of labour that made it
 * work: a tool row's *button* selects which settings show, and its *eye*
 * toggles what is drawn. Those are separate on purpose -- you routinely want a
 * slice and streamlines both visible while tuning only one of them.
 *
 * The panels are kept mounted and merely hidden, as they were under `v-show`,
 * so a tool switch does not throw away a half-typed value or a draft that has
 * not been applied yet.
 */

// Tool key -> icon, kept beside BUILDERS so adding a tool is one place.
const ICONS = {
  square: IconSquare,
  cube: IconCube,
  blur: IconBlur,
  stream: IconVector,
  arrow: IconArrowUpRight,
  home: IconHome,
}

const BUILDERS = {
  cutplane: CutPlaneTool,
  boundary: BoundaryTool,
  contour: ContourTool,
  stream: StreamTool,
  glyph: GlyphTool,
  geometry: GeometryTool,
}

export function SidePane({
  meta, request, setRequest, appearance, setAppearance, setStyle, plane,
  comets, setComets, canAnimate,
}) {
  const active = appearance.activeTool || 'cutplane'
  const setVisible = (part, on) => setAppearance({
    visible: { ...appearance.visible, [part]: on },
  })

  return (
    <div className="sidepane">
      <Select
        {...SELECT}
        data={meta?.cases || []}
        value={request.case || null}
        onChange={(v) => v && setRequest({ case: v })}
        allowDeselect={false}
        mb={4}
        data-ctl="case"
      />
      <Text size="xs" c="dimmed" mb="sm">
        {meta
          ? `${meta.cells.toLocaleString()} cells · ${meta.times.length} time step${meta.times.length === 1 ? '' : 's'}`
          : 'loading…'}
      </Text>

      <div className="toolstack">
        {TOOLS.map(({ key, title, icon }) => {
          const part = TOOL_PART[key]
          const on = appearance.visible[part] !== false
          const disabled = key === 'geometry' && meta && !meta.hasGeometry
          const Icon = ICONS[icon]
          const Eye = on ? IconEye : IconEyeOff
          return (
            <div key={key} className={`toolrow${active === key ? ' active' : ''}`}>
              <button
                type="button"
                className="toolbtn"
                onClick={() => setAppearance({ activeTool: key })}
                data-ctl={`tool-${key}`}
              >
                <Icon size={16} stroke={1.6} className="toolicon" />
                {title}
              </button>
              <Tooltip label={on ? 'Hide' : 'Show'} openDelay={600}>
                <ActionIcon
                  variant="subtle"
                  size="sm"
                  color={on ? 'blue' : 'gray'}
                  disabled={disabled}
                  onClick={() => setVisible(part, !on)}
                  data-ctl={`show-${key}`}
                  aria-label={`${on ? 'Hide' : 'Show'} ${title}`}
                >
                  <Eye size={16} stroke={1.6} />
                </ActionIcon>
              </Tooltip>
            </div>
          )
        })}
      </div>

      <Divider my="sm" />

      {meta ? TOOLS.map(({ key, title, kind }) => {
        const Tool = BUILDERS[key]
        const part = TOOL_PART[key]
        return (
          <div key={key} hidden={active !== key} className="toolpanel">
            <Head kind={kind}>{title}</Head>
            <Tool
              meta={meta}
              request={request}
              setRequest={setRequest}
              style={appearance.styles[part] || {}}
              setStyle={(patch) => setStyle(part, patch)}
              plane={plane}
              comets={comets}
              setComets={setComets}
              canAnimate={canAnimate}
            />
          </div>
        )
      }) : null}

      {/* Scene lighting: global, mostly fine-tuning, so it lives collapsed at
          the bottom of the pane as it does in the Trame app. Here the sliders
          drive shader uniforms rather than actor properties, so they are live
          and free -- the light kit toggle just zeroes the directional term. */}
      <Accordion variant="filled" chevronPosition="left" mt="md">
        <Accordion.Item value="lighting">
          <Accordion.Control icon={<IconBulb size={15} stroke={1.6} />}>
            <Text size="xs" fw={600} tt="uppercase" c="dimmed">Lighting</Text>
          </Accordion.Control>
          <Accordion.Panel>
            <Group justify="space-between" mb={6}>
              <Text size="xs" c="dimmed">Light kit</Text>
              <ActionIcon
                variant="subtle"
                size="sm"
                color={appearance.lighting.lightKit ? 'blue' : 'gray'}
                onClick={() => setAppearance({
                  lighting: { ...appearance.lighting, lightKit: !appearance.lighting.lightKit },
                })}
                data-ctl="light-kit"
                aria-label="Toggle the light kit"
              >
                <IconBulb size={15} stroke={1.6} />
              </ActionIcon>
            </Group>
            <LabelledSlider
              label="Ambient"
              min={0}
              max={1}
              step={0.05}
              live
              value={appearance.lighting.ambient}
              onChange={(v) => setAppearance({
                lighting: { ...appearance.lighting, ambient: v },
              })}
              data-ctl="light-ambient"
            />
            <LabelledSlider
              label="Diffuse"
              min={0}
              max={1}
              step={0.05}
              live
              value={appearance.lighting.diffuse}
              onChange={(v) => setAppearance({
                lighting: { ...appearance.lighting, diffuse: v },
              })}
              data-ctl="light-diffuse"
            />
          </Accordion.Panel>
        </Accordion.Item>
      </Accordion>
    </div>
  )
}
