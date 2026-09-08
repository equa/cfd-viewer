import { useEffect, useRef } from 'react'

/*
 * Keyboard shortcuts, matching the Trame app's set: x/y/z look down an axis
 * (shift for the negative direction), r reframes, f sets the centre of rotation
 * to the point under the cursor (ParaView's focus).
 *
 * In the Trame app these had to be a window listener that *clicked a CSS
 * selector*, because there was no JS→Python path for a plain action, and `f`
 * additionally needed a server trigger, a vtkCellPicker and a camera push. Here
 * they are function calls: the actions live in the same runtime as the keys.
 */

const VIEWS = {
  x: '+x', X: '-x',
  y: '+y', Y: '-y',
  z: '+z', Z: '-z',
}

export function useShortcuts({ onView, onReset, onPick }) {
  // Read the handlers through a ref so the listener is installed once and never
  // goes stale -- re-binding on every render would drop keypresses mid-render.
  const handlers = useRef({ onView, onReset, onPick })
  handlers.current = { onView, onReset, onPick }

  useEffect(() => {
    const pointer = { x: 0, y: 0 }
    const move = (e) => { pointer.x = e.clientX; pointer.y = e.clientY }

    const key = (e) => {
      if (e.ctrlKey || e.altKey || e.metaKey) return
      // Never steal a key from a field the user is typing in.
      const t = e.target
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return

      if (VIEWS[e.key]) {
        handlers.current.onView(VIEWS[e.key])
        e.preventDefault()
        return
      }
      if (e.key === 'r' || e.key === 'R') {
        handlers.current.onReset()
        e.preventDefault()
        return
      }
      if (e.key === 'f' || e.key === 'F') {
        // Only over the canvas: f elsewhere on the page is not aimed at the scene.
        const el = document.elementFromPoint(pointer.x, pointer.y)
        if (el?.tagName !== 'CANVAS') return
        handlers.current.onPick(pointer.x, pointer.y)
        e.preventDefault()
      }
    }

    window.addEventListener('mousemove', move, true)
    window.addEventListener('keydown', key, true)
    return () => {
      window.removeEventListener('mousemove', move, true)
      window.removeEventListener('keydown', key, true)
    }
  }, [])
}

export const SHORTCUT_HELP = [
  ['x / y / z', 'look down the axis (shift = other side)'],
  ['r', 'reframe the domain'],
  ['f', 'centre of rotation under the cursor'],
  ['drag', 'left rotate · middle pan · right zoom'],
]
