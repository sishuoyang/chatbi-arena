import { BaseEdge, EdgeLabelRenderer, getBezierPath } from '@xyflow/react'

// A data-flow edge: a faint base path, a moving dashed overlay, and a packet
// (circle) that travels along the path to convey direction of flow.
export default function AnimatedFlowEdge({
  id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition,
  label, data, markerEnd,
}) {
  const [path, labelX, labelY] = getBezierPath({
    sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition,
  })
  const color = data?.color || '#8b96a8'

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd}
        style={{ stroke: color, strokeOpacity: 0.35, strokeWidth: 2 }} />
      <path d={path} fill="none" stroke={color} strokeWidth={2}
        strokeDasharray="6 10" strokeLinecap="round" className="flow-dash" />
      <circle r={4.5} fill={color} className="flow-packet">
        <animateMotion dur="2.6s" repeatCount="indefinite" path={path} rotate="auto" />
      </circle>
      {label && (
        <EdgeLabelRenderer>
          <div
            className="edge-label"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              '--edge-color': color,
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}
