package com.chapple.nomods5;

import io.netty.buffer.ByteBuf;
import net.minecraft.network.codec.ByteBufCodecs;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.resources.ResourceLocation;
import net.neoforged.neoforge.network.handling.IPayloadContext;

/**
 * The hello packet: one string, sent once when a player joins.
 */
public record HelloPayload(String message) implements CustomPacketPayload {
    public static final Type<HelloPayload> TYPE =
            new Type<>(ResourceLocation.fromNamespaceAndPath(NoMods5.MOD_ID, "hello"));

    public static final StreamCodec<ByteBuf, HelloPayload> STREAM_CODEC = StreamCodec.composite(
            ByteBufCodecs.STRING_UTF8, HelloPayload::message,
            HelloPayload::new
    );

    public static void handle(HelloPayload payload, IPayloadContext context) {
        NoMods5.LOGGER.info("{} received hello packet: {}", NoMods5.MOD_ID, payload.message());
    }

    @Override
    public Type<? extends CustomPacketPayload> type() {
        return TYPE;
    }
}
