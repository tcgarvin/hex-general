// Which world-screen layout to draw: the phone layout below 768 px wide, or on a
// touch screen at most 500 px tall (a phone turned sideways); else the desktop.

import { useEffect, useState } from "react";

export const PHONE_QUERY = "(max-width: 767px), (pointer: coarse) and (max-height: 500px)";

export function usePhoneLayout(): boolean {
  const [phone, setPhone] = useState(() => window.matchMedia(PHONE_QUERY).matches);
  useEffect(() => {
    const list = window.matchMedia(PHONE_QUERY);
    const onChange = () => setPhone(list.matches);
    list.addEventListener("change", onChange);
    return () => list.removeEventListener("change", onChange);
  }, []);
  return phone;
}
