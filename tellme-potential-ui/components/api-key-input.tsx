'use client'

import { useEffect, useRef, useState } from 'react'

import type { ApiKeyOriginPolicy } from '@/lib/security/api-key-origin'

type KeySource = 'empty' | 'manual input' | 'browser autofill'

interface ApiKeyInputProps {
  name: string
  value: string
  onValueChange: (value: string) => void
  placeholder: string
  policy: ApiKeyOriginPolicy
  disabled?: boolean
}

export function ApiKeyInput({
  name,
  value,
  onValueChange,
  placeholder,
  policy,
  disabled = false,
}: ApiKeyInputProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const valueRef = useRef(value)
  const onValueChangeRef = useRef(onValueChange)
  const [source, setSource] = useState<KeySource>('empty')

  useEffect(() => {
    valueRef.current = value
    onValueChangeRef.current = onValueChange
  }, [onValueChange, value])

  useEffect(() => {
    if (!policy.canUseApiKeys) return

    // Password managers may set the DOM value without firing React's onChange.
    const synchronizeAutofill = () => {
      const inputValue = inputRef.current?.value ?? ''
      if (inputValue !== valueRef.current) {
        valueRef.current = inputValue
        setSource(inputValue ? 'browser autofill' : 'empty')
        onValueChangeRef.current(inputValue)
      }
    }

    synchronizeAutofill()
    const interval = window.setInterval(synchronizeAutofill, 300)
    return () => window.clearInterval(interval)
  }, [policy.canUseApiKeys])

  const updateManually = (nextValue: string) => {
    valueRef.current = nextValue
    setSource(nextValue ? 'manual input' : 'empty')
    onValueChangeRef.current(nextValue)
  }

  const keyLength = value.trim().length

  return (
    <>
      <input
        ref={inputRef}
        name={name}
        type="password"
        value={value}
        onInput={(event) => updateManually(event.currentTarget.value)}
        onFocus={() => {
          const inputValue = inputRef.current?.value ?? ''
          if (inputValue !== valueRef.current) {
            valueRef.current = inputValue
            setSource(inputValue ? 'browser autofill' : 'empty')
            onValueChangeRef.current(inputValue)
          }
        }}
        placeholder={placeholder}
        disabled={disabled || !policy.canUseApiKeys}
        className="form-field disabled:cursor-not-allowed disabled:opacity-60"
      />
      <p className="mt-1 text-[10px] text-muted-foreground" aria-live="polite">
        {keyLength > 0
          ? `Detected · ${keyLength} characters · ${source}`
          : disabled
            ? 'No API key is required for the local provider.'
            : policy.canUseApiKeys
            ? 'No key entered for this origin.'
            : policy.message}
      </p>
    </>
  )
}
