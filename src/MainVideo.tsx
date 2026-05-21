import {
  AbsoluteFill,
  Audio,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig
} from "remotion";
import React from "react";

export type MainVideoProps = {
  text: string;
  audioUrl?: string;
  title?: string;
  subtitle?: string;
  bgColor1?: string;
  bgColor2?: string;
};

export const MainVideo: React.FC<MainVideoProps> = ({
  text,
  audioUrl = "rattle_speech.mp3",
  title = "RATTLE DAILY BROADCAST",
  subtitle = "AI EXPLORATION LOG",
  bgColor1 = "#090d16",
  bgColor2 = "#0b1528"
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  // Split text into meaningful sentences/segments
  const segments = text
    .split(/[.!?;\n]+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 5);

  const totalSegments = segments.length || 1;
  const framesPerSegment = Math.floor(durationInFrames / totalSegments);

  // Background breathing animation
  const bgScale = interpolate(
    Math.sin((frame / fps) * 0.5),
    [-1, 1],
    [1.0, 1.1]
  );

  return (
    <AbsoluteFill
      style={{
        background: `linear-gradient(135deg, ${bgColor1} 0%, ${bgColor2} 100%)`,
        fontFamily: "'Outfit', 'Inter', sans-serif",
        color: "#ffffff",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        padding: "80px 60px",
        boxSizing: "border-box"
      }}
    >
      {/* Background ambient glowing spheres */}
      <div
        style={{
          position: "absolute",
          width: "600px",
          height: "600px",
          borderRadius: "50%",
          background: "radial-gradient(circle, rgba(99, 102, 241, 0.15) 0%, rgba(0,0,0,0) 70%)",
          top: "-100px",
          left: "-100px",
          transform: `scale(${bgScale})`,
          pointerEvents: "none"
        }}
      />
      <div
        style={{
          position: "absolute",
          width: "500px",
          height: "500px",
          borderRadius: "50%",
          background: "radial-gradient(circle, rgba(236, 72, 153, 0.12) 0%, rgba(0,0,0,0) 70%)",
          bottom: "100px",
          right: "-100px",
          transform: `scale(${2.1 - bgScale})`,
          pointerEvents: "none"
        }}
      />

      {/* Header bar */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          width: "100%",
          zIndex: 10
        }}
      >
        <div style={{ display: "flex", flexDirection: "column" }}>
          <h2
            style={{
              margin: 0,
              fontSize: "32px",
              fontWeight: 800,
              letterSpacing: "4px",
              background: "linear-gradient(90deg, #818cf8, #ec4899)",
              WebkitBackgroundClip: "text",
              WebkitTextFillColor: "transparent"
            }}
          >
            {title}
          </h2>
          <span
            style={{
              fontSize: "18px",
              color: "#94a3b8",
              letterSpacing: "2px",
              marginTop: "4px"
            }}
          >
            {subtitle}
          </span>
        </div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            background: "rgba(255, 255, 255, 0.05)",
            padding: "10px 20px",
            borderRadius: "50px",
            border: "1px solid rgba(255, 255, 255, 0.1)"
          }}
        >
          <div
            style={{
              width: "12px",
              height: "12px",
              borderRadius: "50%",
              backgroundColor: "#22c55e",
              marginRight: "10px",
              boxShadow: "0 0 12px #22c55e",
              opacity: Math.sin(frame * 0.15) > 0 ? 1 : 0.4
            }}
          />
          <span style={{ fontSize: "16px", fontWeight: "bold", letterSpacing: "1px" }}>
            LIVE
          </span>
        </div>
      </div>

      {/* Main Glassmorphism Display Card */}
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          margin: "120px 0",
          zIndex: 10
        }}
      >
        <div
          style={{
            width: "100%",
            minHeight: "450px",
            background: "rgba(255, 255, 255, 0.03)",
            backdropFilter: "blur(20px)",
            borderRadius: "32px",
            border: "1px solid rgba(255, 255, 255, 0.08)",
            boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.5)",
            padding: "50px 40px",
            boxSizing: "border-box",
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
            alignItems: "center",
            position: "relative"
          }}
        >
          {/* Subtle neon corner glows */}
          <div style={{ position: "absolute", top: 0, left: 0, width: "30px", height: "30px", borderTop: "2px solid #818cf8", borderLeft: "2px solid #818cf8", borderTopLeftRadius: "32px" }} />
          <div style={{ position: "absolute", bottom: 0, right: 0, width: "30px", height: "30px", borderBottom: "2px solid #ec4899", borderRight: "2px solid #ec4899", borderBottomRightRadius: "32px" }} />

          {/* Active Segment Rendering with transitions */}
          {segments.map((segmentText, index) => {
            const startFrame = index * framesPerSegment;
            const endFrame = startFrame + framesPerSegment;
            const isActive = frame >= startFrame && frame < endFrame;

            if (!isActive) return null;

            // Smooth fade-in & slide-up using Remotion spring
            const entryProgress = spring({
              frame: frame - startFrame,
              fps,
              config: { damping: 15 }
            });

            const opacity = entryProgress;
            const translateY = interpolate(entryProgress, [0, 1], [30, 0]);

            return (
              <div
                key={index}
                style={{
                  opacity,
                  transform: `translateY(${translateY}px)`,
                  width: "100%",
                  textAlign: "center"
                }}
              >
                <p
                  style={{
                    fontSize: "42px",
                    lineHeight: "1.5",
                    fontWeight: 500,
                    margin: 0,
                    color: "#f8fafc",
                    letterSpacing: "-0.5px"
                  }}
                >
                  "{segmentText}."
                </p>
              </div>
            );
          })}
        </div>
      </div>

      {/* Footer Area: Audio waveform visualizer */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          width: "100%",
          zIndex: 10
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            height: "80px",
            gap: "8px"
          }}
        >
          {Array.from({ length: 18 }).map((_, i) => {
            // Organic bouncing wave effect using sine waves over the current frame
            const pulse = Math.sin((frame * 0.15) + (i * 0.5));
            const barHeight = interpolate(pulse, [-1, 1], [15, 65]);
            const color = i % 2 === 0 ? "#818cf8" : "#ec4899";

            return (
              <div
                key={i}
                style={{
                  width: "8px",
                  height: `${barHeight}px`,
                  backgroundColor: color,
                  borderRadius: "4px",
                  opacity: interpolate(barHeight, [15, 65], [0.4, 0.9]),
                  boxShadow: `0 0 10px ${color}`
                }}
              />
            );
          })}
        </div>
        <span
          style={{
            fontSize: "16px",
            color: "#64748b",
            letterSpacing: "3px",
            marginTop: "16px"
          }}
        >
          RATTLE AI SYSTEMS INC.
        </span>
      </div>

      {/* Voice audio playing */}
      <Audio src={staticFile(audioUrl)} />
    </AbsoluteFill>
  );
};
