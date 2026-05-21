import { Composition, staticFile } from "remotion";
import { getAudioDurationInSeconds } from "@remotion/media-utils";
import { MainVideo, MainVideoProps } from "./MainVideo";

export const RemotionRoot = () => {
  return (
    <Composition
      id="MainVideo"
      component={MainVideo}
      durationInFrames={300} // Fallback default
      fps={30}
      width={1080}
      height={1920} // Vertical video (Tiktok/Reels format)
      defaultProps={{
        text: "Hola, soy Rattle, tu inteligencia artificial autónoma.",
        audioUrl: "rattle_speech.mp3",
        title: "RATTLE INTEL",
        subtitle: "Daily Broadcast",
        bgColor1: "#0f172a",
        bgColor2: "#1e1b4b"
      } as MainVideoProps}
      calculateMetadata={async ({ props }) => {
        const audioUrl = props.audioUrl || "rattle_speech.mp3";
        let durationInSeconds = 10;
        try {
          durationInSeconds = await getAudioDurationInSeconds(staticFile(audioUrl));
        } catch (e) {
          console.error("No se pudo obtener la duración del audio:", e);
        }
        
        // Return dynamic duration matching the audio file duration
        return {
          durationInFrames: Math.ceil(durationInSeconds * 30) + 15, // 30 FPS + 15 frames padding
          props
        };
      }}
    />
  );
};
