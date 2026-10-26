import type { components } from './generated'
export type Model<K extends keyof components['schemas']> = components['schemas'][K]
export type Action = Model<'ActionAvailability'>
export type Identity = Model<'CurrentIdentityResponse'>
export type Project = Model<'ProjectResponse'>
export type Pipeline = Model<'PipelineResponse'>
export type Version = Model<'PipelineVersionResponse'>
export type Design = Model<'DesignResponse'>
export type Profile = Model<'MetadataProfileResponse'>
export type Connection = Model<'ConnectionResponse'>
export type Preparation = Model<'PreparationResponse'>
export type Run = Model<'ExecutionRunResponse'>
export interface Page<T> { items:T[]; has_more:boolean; next_cursor:string|null; request_id:string }
export interface Result<T> { data:T; etag:string|null; requestId:string }
export type JsonRecord = Record<string, unknown>
export function isRecord(value:unknown): value is JsonRecord { return typeof value==='object' && value!==null && !Array.isArray(value) }
