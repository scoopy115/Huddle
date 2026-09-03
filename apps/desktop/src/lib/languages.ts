import { languageName } from "./format";

/**
 * Languages offered for the spoken language and for the notes language, shown by their
 * names in the UI language. Whisper transcribes all of them; the notes LLM writes in them.
 * The engine keeps the same list (providers/summarize.py LANG_NAMES).
 */
export const LANGUAGE_CODES = [
  "en", "nl", "de", "fr", "es", "it", "pt", "pl", "sv", "da", "no", "fi", "is", "tr", "cs", "sk", "sl", "hr", "sr", "bs", "bg", "mk", "el",
  "hu", "ro", "uk", "ru", "be", "et", "lv", "lt", "ga", "cy", "ca", "eu", "gl", "ar", "he", "fa", "hi", "bn", "ur", "ta", "te", "ml", "kn",
  "id", "ms", "vi", "th", "tl", "ja", "ko", "zh", "sw", "af",
];
export const languageOptions = () => LANGUAGE_CODES.map((c) => ({ code: c, name: languageName(c) })).sort((a, b) => a.name.localeCompare(b.name));

/** The system language when Huddle supports it, otherwise English. */
export const systemLanguage = (locale: string | null | undefined): string => {
  const lang = (locale ?? "").split(/[-_]/)[0].toLowerCase();
  return LANGUAGE_CODES.includes(lang) ? lang : "en";
};
