// Adapted from Vercel AI Elements' PromptInput. Attachments here are server uploads shown by the
// caller, so the AI SDK file parts, blob URLs, paste and drop handling, speech, select, command
// and tab parts are left out: PromptInput hands picked files to `onFiles`.

import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupTextarea,
} from "@/components/ui/input-group";
import { cn } from "@/lib/utils";
import { ArrowUpIcon, Loader2Icon, PlusIcon, SquareIcon, XIcon } from "lucide-react";
import {
  type ChangeEventHandler,
  Children,
  type ComponentProps,
  createContext,
  type FormEvent,
  type HTMLAttributes,
  type KeyboardEventHandler,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from "react";

export type PromptInputStatus = "ready" | "submitted" | "streaming" | "error";

type FilesContext = {
  openFileDialog: () => void;
  disabled: boolean;
};

const PromptInputFilesContext = createContext<FilesContext | null>(null);

export const usePromptInputFiles = () => {
  const context = useContext(PromptInputFilesContext);
  if (!context) {
    throw new Error("usePromptInputFiles must be used within a PromptInput");
  }
  return context;
};

export type PromptInputProps = Omit<HTMLAttributes<HTMLFormElement>, "onSubmit"> & {
  accept?: string;
  multiple?: boolean;
  /** Dims the input and disables the file picker. */
  disabled?: boolean;
  /** Files picked in the file dialog. */
  onFiles?: (files: File[]) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
};

export const PromptInput = ({
  className,
  accept,
  multiple,
  disabled = false,
  onFiles,
  onSubmit,
  children,
  ...props
}: PromptInputProps) => {
  const inputRef = useRef<HTMLInputElement | null>(null);

  const openFileDialog = useCallback(() => {
    inputRef.current?.click();
  }, []);

  const handleChange: ChangeEventHandler<HTMLInputElement> = (event) => {
    const files = Array.from(event.currentTarget.files ?? []);
    if (files.length > 0) {
      onFiles?.(files);
    }
    // Reset so the same file can be picked again
    event.currentTarget.value = "";
  };

  const context = useMemo(() => ({ openFileDialog, disabled }), [openFileDialog, disabled]);

  return (
    <PromptInputFilesContext.Provider value={context}>
      <input
        accept={accept}
        aria-label="Upload files"
        className="hidden"
        disabled={disabled}
        multiple={multiple}
        onChange={handleChange}
        ref={inputRef}
        title="Upload files"
        type="file"
      />
      <form
        className={cn("w-full", className)}
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit(event);
        }}
        {...props}
      >
        <InputGroup
          className={cn(
            "overflow-hidden rounded-2xl bg-background shadow-xs has-disabled:bg-background has-disabled:opacity-100",
            disabled && "opacity-50 has-disabled:opacity-50",
          )}
        >
          {children}
        </InputGroup>
      </form>
    </PromptInputFilesContext.Provider>
  );
};

export type PromptInputBodyProps = HTMLAttributes<HTMLDivElement>;

export const PromptInputBody = ({ className, ...props }: PromptInputBodyProps) => (
  <div className={cn("contents", className)} {...props} />
);

export type PromptInputTextareaProps = ComponentProps<typeof InputGroupTextarea>;

/** Enter submits the form; Shift+Enter adds a line. */
export const PromptInputTextarea = ({
  className,
  placeholder = "What would you like to know?",
  onKeyDown,
  ...props
}: PromptInputTextareaProps) => {
  const [isComposing, setIsComposing] = useState(false);

  const handleKeyDown: KeyboardEventHandler<HTMLTextAreaElement> = (e) => {
    onKeyDown?.(e);
    if (e.defaultPrevented || e.key !== "Enter") {
      return;
    }
    if (isComposing || e.nativeEvent.isComposing || e.shiftKey) {
      return;
    }
    e.preventDefault();

    // Check if the submit button is disabled before submitting
    const form = e.currentTarget.form;
    const submitButton = form?.querySelector('button[type="submit"]') as HTMLButtonElement | null;
    if (!submitButton || submitButton.disabled) {
      return;
    }

    form?.requestSubmit();
  };

  return (
    <InputGroupTextarea
      className={cn("field-sizing-content max-h-48 min-h-16", className)}
      name="message"
      onCompositionEnd={() => setIsComposing(false)}
      onCompositionStart={() => setIsComposing(true)}
      onKeyDown={handleKeyDown}
      placeholder={placeholder}
      {...props}
    />
  );
};

export type PromptInputHeaderProps = Omit<ComponentProps<typeof InputGroupAddon>, "align">;

export const PromptInputHeader = ({ className, ...props }: PromptInputHeaderProps) => (
  <InputGroupAddon align="block-end" className={cn("order-first flex-wrap gap-1", className)} {...props} />
);

export type PromptInputFooterProps = Omit<ComponentProps<typeof InputGroupAddon>, "align">;

export const PromptInputFooter = ({ className, ...props }: PromptInputFooterProps) => (
  <InputGroupAddon align="block-end" className={cn("justify-between gap-1", className)} {...props} />
);

export type PromptInputToolsProps = HTMLAttributes<HTMLDivElement>;

export const PromptInputTools = ({ className, ...props }: PromptInputToolsProps) => (
  <div className={cn("flex min-w-0 items-center gap-1", className)} {...props} />
);

export type PromptInputButtonProps = ComponentProps<typeof InputGroupButton>;

export const PromptInputButton = ({ variant = "ghost", className, size, ...props }: PromptInputButtonProps) => {
  const newSize = size ?? (Children.count(props.children) > 1 ? "sm" : "icon-sm");

  return <InputGroupButton className={cn(className)} size={newSize} type="button" variant={variant} {...props} />;
};

export type PromptInputAddFilesProps = PromptInputButtonProps;

/** Opens the file dialog. */
export const PromptInputAddFiles = ({ children, disabled, ...props }: PromptInputAddFilesProps) => {
  const files = usePromptInputFiles();
  return (
    <PromptInputButton disabled={disabled ?? files.disabled} onClick={files.openFileDialog} {...props}>
      {children ?? <PlusIcon className="size-4" />}
    </PromptInputButton>
  );
};

export type PromptInputSubmitProps = ComponentProps<typeof InputGroupButton> & {
  status?: PromptInputStatus;
};

export const PromptInputSubmit = ({
  className,
  variant = "default",
  size = "icon-sm",
  status,
  children,
  ...props
}: PromptInputSubmitProps) => {
  let Icon = <ArrowUpIcon className="size-4" />;

  if (status === "submitted") {
    Icon = <Loader2Icon className="size-4 animate-spin" />;
  } else if (status === "streaming") {
    Icon = <SquareIcon className="size-3.5 fill-current" />;
  } else if (status === "error") {
    Icon = <XIcon className="size-4" />;
  }

  return (
    <InputGroupButton
      aria-label="Submit"
      className={cn(className)}
      size={size}
      type="submit"
      variant={variant}
      {...props}
    >
      {children ?? Icon}
    </InputGroupButton>
  );
};
