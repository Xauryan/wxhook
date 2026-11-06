import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Avatar, EmptyState, ErrorState, SectionLink } from "./ui";

describe("shared UI states", () => {
  it("uses real names for stable avatar fallbacks", () => {
    render(<Avatar name="本地联系人" size={40} />);
    expect(screen.getByLabelText("本地联系人的占位头像")).toHaveTextContent("本地");
  });

  it("exposes empty and error semantics", () => {
    const retry = vi.fn();
    render(<><EmptyState title="没有媒体" detail="资源文件不存在" /><ErrorState error={new Error("读取失败")} retry={retry} /></>);
    expect(screen.getByText("没有媒体")).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("读取失败");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("renders section actions as keyboard-accessible buttons", () => {
    const onClick = vi.fn();
    render(<SectionLink onClick={onClick}>查看会话</SectionLink>);
    const button = screen.getByRole("button", { name: /查看会话/ });
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });
});
