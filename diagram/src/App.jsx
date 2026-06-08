import { useMemo } from 'react'
import {
  ReactFlow, Background, Controls, MiniMap, Panel, MarkerType,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { nodes as rawNodes, edges as rawEdges, EDGE_COLORS } from './graph.js'
import CardNode from './nodes/CardNode.jsx'
import ZoneNode from './nodes/ZoneNode.jsx'
import AnimatedFlowEdge from './edges/AnimatedFlowEdge.jsx'

const nodeTypes = { card: CardNode, zone: ZoneNode }
const edgeTypes = { flow: AnimatedFlowEdge }

const LEGEND = [
  ['OLTP + CDC data', EDGE_COLORS.data],
  ['Bedrock inference', EDGE_COLORS.ai],
  ['read-only query', EDGE_COLORS.read],
  ['orchestration', EDGE_COLORS.control],
  ['LangFuse traces', EDGE_COLORS.trace],
  ['OTel telemetry', EDGE_COLORS.telemetry],
]

export default function App() {
  const edges = useMemo(
    () => rawEdges.map((e) => ({
      ...e,
      markerEnd: { type: MarkerType.ArrowClosed, color: e.data.color, width: 16, height: 16 },
    })),
    [],
  )

  return (
    <div style={{ width: '100vw', height: '100vh', background: '#0e1116' }}>
      <ReactFlow
        nodes={rawNodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        minZoom={0.2}
        proOptions={{ hideAttribution: true }}
        nodesDraggable
        elevateNodesOnSelect
      >
        <Background color="#1c2230" gap={26} size={1.5} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable nodeColor={(n) => (n.type === 'zone' ? 'transparent' : (n.data?.accent || '#888'))}
          maskColor="rgba(14,17,22,0.7)" style={{ background: '#161b22' }} />

        <Panel position="top-left" className="title-panel">
          <div className="title-main">ChatBI&nbsp;<b>Arena</b> — architecture &amp; data flow</div>
          <div className="title-sub">
            NL→SQL agent benchmark · Aurora→ClickPipes CDC→ClickHouse · Bedrock · LangFuse · ClickStack
          </div>
        </Panel>

        <Panel position="bottom-right" className="legend-panel">
          {LEGEND.map(([label, color]) => (
            <div key={label} className="legend-row">
              <span className="legend-swatch" style={{ background: color }} />
              {label}
            </div>
          ))}
        </Panel>
      </ReactFlow>
    </div>
  )
}
