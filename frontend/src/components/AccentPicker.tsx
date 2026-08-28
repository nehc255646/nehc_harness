import { ACCENTS, type AccentId } from "../theme/themes";
import { useThemeStore } from "../store/themeStore";

export default function AccentPicker() {
  const { accent, setAccent } = useThemeStore();
  return (
    <div className="flex items-center gap-1.5 pr-1" title="强调色">
      {(Object.keys(ACCENTS) as AccentId[]).map((id) => (
        <button
          key={id}
          aria-label={ACCENTS[id].label}
          onClick={() => setAccent(id)}
          className="h-3.5 w-3.5 rounded-full transition hover:scale-110"
          style={{
            background: ACCENTS[id].accent,
            outline: accent === id ? `2px solid ${ACCENTS[id].accent}` : "2px solid transparent",
            outlineOffset: "2px",
            opacity: accent === id ? 1 : 0.55,
          }}
        />
      ))}
    </div>
  );
}
