import { ACCENTS, type AccentId } from "../theme/themes";
import { useThemeStore } from "../store/themeStore";

export default function AccentPicker() {
  const { accent, setAccent } = useThemeStore();
  return (
    <div className="flex items-center gap-1" title="强调色">
      {(Object.keys(ACCENTS) as AccentId[]).map((id) => (
        <button
          key={id}
          aria-label={ACCENTS[id].label}
          onClick={() => setAccent(id)}
          className={`h-3.5 w-3.5 rounded-full border ${accent === id ? "ring-2 ring-offset-1 ring-offset-black" : "opacity-70 hover:opacity-100"}`}
          style={{
            background: ACCENTS[id].accent,
            borderColor: accent === id ? ACCENTS[id].accent : "#3f3f46",
            // ring color via boxShadow because ring-* is static
            boxShadow: accent === id ? `0 0 0 2px ${ACCENTS[id].accent}` : undefined,
          }}
        />
      ))}
    </div>
  );
}
